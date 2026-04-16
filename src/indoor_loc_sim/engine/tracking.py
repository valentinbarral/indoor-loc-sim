from __future__ import annotations

from collections.abc import Callable

import numpy as np
from numpy.linalg import cholesky, norm

from indoor_loc_sim.core.models import Beacon, Door, Wall
from indoor_loc_sim.core.trajectory import TrajectoryPoint
from indoor_loc_sim.engine.geometry import count_wall_crossings
from indoor_loc_sim.engine.signals import BeaconSignal, SignalType
from indoor_loc_sim.engine.fingerprint import CancelledError


def _u2rss(
    state: np.ndarray,
    beacon_positions: np.ndarray,
    rssi_at_ref: float = -59.0,
    path_loss_exponent: float = 2.0,
    d0: float = 1.0,
    wall_attenuation: np.ndarray | None = None,
) -> np.ndarray:
    dx = state[0] - beacon_positions[:, 0]
    dy = state[1] - beacon_positions[:, 1]
    dz = state[2] - beacon_positions[:, 2]
    distances = np.sqrt(dx**2 + dy**2 + dz**2)
    distances = np.maximum(distances, 0.1)
    rss = rssi_at_ref - 10.0 * path_loss_exponent * np.log10(distances / d0)
    if wall_attenuation is not None:
        rss = rss - wall_attenuation
    return rss


def _u2tof(state: np.ndarray, beacon_positions: np.ndarray) -> np.ndarray:
    dx = state[0] - beacon_positions[:, 0]
    dy = state[1] - beacon_positions[:, 1]
    dz = state[2] - beacon_positions[:, 2]
    distances = np.sqrt(dx**2 + dy**2 + dz**2)
    return distances / SPEED_OF_LIGHT


def _u2distance(state: np.ndarray, beacon_positions: np.ndarray) -> np.ndarray:
    dx = state[0] - beacon_positions[:, 0]
    dy = state[1] - beacon_positions[:, 1]
    dz = state[2] - beacon_positions[:, 2]
    return np.sqrt(dx**2 + dy**2 + dz**2)


def _build_cv_process_noise(
    dt: float,
    n_states: int,
    process_noise_std: float,
) -> np.ndarray:
    """Build a proper constant-velocity process noise matrix.

    Uses the piecewise-constant white-noise acceleration model.  The
    ``process_noise_std`` parameter is interpreted as the standard deviation
    of the (random) acceleration, so the spectral density is ``q = σ²``.

    For a 5-state vector ``[x, y, z, vx, vy]`` the block structure is::

        Q_pos-pos   = dt⁴/4 · q
        Q_pos-vel   = dt³/2 · q    (cross-covariance)
        Q_vel-vel   = dt²   · q

    This keeps the filter stable regardless of the sampling rate and prevents
    divergence when the user changes the process noise parameter.
    """
    q = process_noise_std**2
    Q = np.zeros((n_states, n_states))

    # Position–position (indices 0,1,2)
    pp = dt**4 / 4.0 * q
    Q[0, 0] = pp
    Q[1, 1] = pp
    Q[2, 2] = pp

    # Position–velocity cross-covariance (0↔3, 1↔4)
    pv = dt**3 / 2.0 * q
    Q[0, 3] = pv
    Q[3, 0] = pv
    Q[1, 4] = pv
    Q[4, 1] = pv

    # Velocity–velocity (indices 3,4)
    vv = dt**2 * q
    Q[3, 3] = vv
    Q[4, 4] = vv

    return Q


def _compute_wall_attenuation(
    state: np.ndarray,
    beacon_positions: np.ndarray,
    walls: list[Wall],
    doors: list[Door],
    wall_attenuation_db: float,
) -> np.ndarray:
    pos = np.real(state[:3]) if np.iscomplexobj(state) else state[:3]
    att = np.zeros(beacon_positions.shape[0])
    if wall_attenuation_db <= 0 or not walls:
        return att
    for j in range(beacon_positions.shape[0]):
        n_walls = count_wall_crossings(pos, beacon_positions[j], walls, doors)
        att[j] = n_walls * wall_attenuation_db
    return att


def _jacobian_complex_step(fn, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    z = fn(x)
    n = len(x)
    m = len(z)
    J = np.zeros((m, n))
    h = n * np.finfo(float).eps
    for k in range(n):
        x1 = x.astype(complex)
        x1[k] += h * 1j
        J[:, k] = np.imag(fn(x1)) / h
    return z, J


def _simulate_acceleration_measurements(
    reference_trajectory: list[TrajectoryPoint],
    timeline: np.ndarray,
    noise_variance: float,
) -> np.ndarray:
    if len(timeline) == 0:
        return np.zeros((0, 2))
    if len(reference_trajectory) < 2:
        return np.zeros((len(timeline), 2))

    ref_times = np.array([p.t for p in reference_trajectory])
    ref_vx = np.array([p.vx for p in reference_trajectory])
    ref_vy = np.array([p.vy for p in reference_trajectory])

    dt = np.diff(ref_times)
    safe_dt = np.where(np.abs(dt) < 1e-10, 1e-10, dt)
    ax = np.zeros(len(reference_trajectory))
    ay = np.zeros(len(reference_trajectory))
    ax[:-1] = np.diff(ref_vx) / safe_dt
    ay[:-1] = np.diff(ref_vy) / safe_dt
    ax[-1] = ax[-2]
    ay[-1] = ay[-2]

    interp_ax = np.interp(timeline, ref_times, ax)
    interp_ay = np.interp(timeline, ref_times, ay)
    accel = np.column_stack((interp_ax, interp_ay))

    noise_std = float(np.sqrt(max(noise_variance, 0.0)))
    if noise_std > 0.0:
        accel = accel + np.random.normal(0.0, noise_std, size=accel.shape)
    return accel


def _select_valid_rss_indices(
    rss_values: np.ndarray,
    min_rss_threshold: float | None,
) -> np.ndarray:
    valid_mask = np.isfinite(rss_values)
    if min_rss_threshold is not None:
        valid_mask &= rss_values >= min_rss_threshold
    return np.where(valid_mask)[0]


# ── Extended Kalman Filter (matches pos2D_EKF_RSS.m) ──


def _ekf_update(
    f_state,
    x: np.ndarray,
    P: np.ndarray,
    h_meas,
    z: np.ndarray,
    Q: np.ndarray,
    R: np.ndarray,
    max_normalized_innovation: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    x1, A = _jacobian_complex_step(f_state, x)
    P = A @ P @ A.T + Q
    # Symmetrise to prevent drift from floating-point accumulation
    P = 0.5 * (P + P.T)

    z1, H = _jacobian_complex_step(h_meas, x1)
    P12 = P @ H.T

    S = H @ P12 + R
    S = 0.5 * (S + S.T)
    # Regularise S so Cholesky never fails on near-singular matrices
    S += 1e-6 * np.eye(S.shape[0])

    try:
        L = cholesky(S)
    except np.linalg.LinAlgError:
        # Fall back to prediction only — skip this measurement
        return x1.real, P.real

    U = np.linalg.solve(L, P12.T).T
    innovation = z - z1
    whitened_innovation = np.linalg.solve(L, innovation)
    if max_normalized_innovation is not None:
        nis = float(whitened_innovation.T @ whitened_innovation)
        if nis > max_normalized_innovation:
            return x1.real, P.real
    x = x1 + U @ whitened_innovation
    P = P - U @ U.T
    # Ensure P stays positive-definite
    P = 0.5 * (P + P.T)
    return x.real, P.real


def estimate_ekf_rss(
    signal: BeaconSignal,
    initial_state: TrajectoryPoint,
    process_noise_std: float = 1.0,
    measurement_noise_std: float = 2.0,
    path_loss_exponent: float = 2.0,
    walls: list[Wall] | None = None,
    doors: list[Door] | None = None,
    wall_attenuation_db: float = 0.0,
    progress_callback: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    rssi_at_ref: float = -59.0,
    d0: float = 1.0,
    min_rss_threshold: float | None = None,
) -> list[TrajectoryPoint]:
    n_states = 5

    beacon_positions = np.array([b.r for b in signal.beacons])

    wall_list = walls or []
    door_list = doors or []
    use_walls = wall_attenuation_db > 0 and bool(wall_list)

    # Large initial covariance so the filter trusts early measurements
    P = np.diag([10.0, 10.0, 1.0, 2.0, 2.0])

    x = np.array(
        [
            initial_state.x,
            initial_state.y,
            initial_state.z,
            initial_state.vx,
            initial_state.vy,
        ]
    )

    result = []
    total_steps = len(signal.timeline)
    for i in range(total_steps):
        if is_cancelled and is_cancelled():
            raise CancelledError()

        t = signal.timeline[i]
        dt = float(t - signal.timeline[i - 1]) if i > 0 else 0.0

        Q = _build_cv_process_noise(dt, n_states, process_noise_std)

        def f_state(state, _dt=dt):
            return np.array(
                [
                    state[0] + _dt * state[3],
                    state[1] + _dt * state[4],
                    state[2],
                    state[3],
                    state[4],
                ]
            )

        meas = signal.measurements[i]
        selected = _select_valid_rss_indices(meas.values, min_rss_threshold)
        if len(selected) == 0:
            x, A = _jacobian_complex_step(f_state, x)
            P = A @ P @ A.T + Q
            P = 0.5 * (P + P.T)
        else:
            bp = beacon_positions[selected]
            z = meas.values[selected]
            if use_walls:
                w_att = _compute_wall_attenuation(
                    x,
                    bp,
                    wall_list,
                    door_list,
                    wall_attenuation_db,
                )
            else:
                w_att = None
            h_meas = (
                lambda state, _bp=bp, a=rssi_at_ref, n=path_loss_exponent, _d0=d0, wa=w_att: (
                    _u2rss(state, _bp, a, n, _d0, wa)
                )
            )
            R = measurement_noise_std**2 * np.eye(len(selected))
            x, P = _ekf_update(
                f_state,
                x,
                P,
                h_meas,
                z,
                Q,
                R,
                max_normalized_innovation=9.0 * len(selected),
            )
        result.append(
            TrajectoryPoint(
                x=float(x[0].real),
                y=float(x[1].real),
                z=float(initial_state.z),
                t=float(t),
            )
        )

        if progress_callback:
            progress_callback(i + 1, total_steps)

    return result


def estimate_ekf_tof(
    signal: BeaconSignal,
    initial_state: TrajectoryPoint,
    process_noise_std: float = 1.0,
    measurement_noise_std: float = 1e-9,
    progress_callback: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[TrajectoryPoint]:
    n_states = 5

    beacon_positions = np.array([b.r for b in signal.beacons])
    n_beacons = len(signal.beacons)

    measurement_noise_distance = SPEED_OF_LIGHT * measurement_noise_std
    R = measurement_noise_distance**2 * np.eye(n_beacons)
    P = np.diag([10.0, 10.0, 1.0, 2.0, 2.0])

    x = np.array(
        [
            initial_state.x,
            initial_state.y,
            initial_state.z,
            initial_state.vx,
            initial_state.vy,
        ]
    )

    result = []
    total_steps = len(signal.timeline)
    for i in range(total_steps):
        if is_cancelled and is_cancelled():
            raise CancelledError()

        t = signal.timeline[i]
        dt = float(t - signal.timeline[i - 1]) if i > 0 else 0.0
        Q = _build_cv_process_noise(dt, n_states, process_noise_std)

        def f_state(state, _dt=dt):
            return np.array(
                [
                    state[0] + _dt * state[3],
                    state[1] + _dt * state[4],
                    state[2],
                    state[3],
                    state[4],
                ]
            )

        meas = signal.measurements[i]
        valid_mask = np.isfinite(meas.values) & (meas.values > 0)
        if np.any(valid_mask):
            z = SPEED_OF_LIGHT * meas.values[valid_mask]
            bp = beacon_positions[valid_mask]
            R_step = measurement_noise_distance**2 * np.eye(len(z))
            h_meas = lambda state, _bp=bp: _u2distance(state, _bp)
            x, P = _ekf_update(f_state, x, P, h_meas, z, Q, R_step)
        else:
            x, A = _jacobian_complex_step(f_state, x)
            P = A @ P @ A.T + Q
            P = 0.5 * (P + P.T)

        result.append(
            TrajectoryPoint(
                x=float(x[0]),
                y=float(x[1]),
                z=float(initial_state.z),
                t=float(t),
            )
        )

        if progress_callback:
            progress_callback(i + 1, total_steps)

    return result


def estimate_ekf_rss_accel(
    signal: BeaconSignal,
    initial_state: TrajectoryPoint,
    reference_trajectory: list[TrajectoryPoint],
    process_noise_std: float = 1.0,
    measurement_noise_std: float = 2.0,
    path_loss_exponent: float = 2.0,
    accel_noise_variance: float = 1e-3,
    walls: list[Wall] | None = None,
    doors: list[Door] | None = None,
    wall_attenuation_db: float = 0.0,
    progress_callback: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    rssi_at_ref: float = -59.0,
    d0: float = 1.0,
    min_rss_threshold: float | None = None,
) -> list[TrajectoryPoint]:
    n_states = 7

    beacon_positions = np.array([b.r for b in signal.beacons])

    wall_list = walls or []
    door_list = doors or []
    use_walls = wall_attenuation_db > 0 and bool(wall_list)

    accel_R = accel_noise_variance * np.eye(2)
    P = np.diag([10.0, 10.0, 1.0, 2.0, 2.0, 1.0, 1.0])

    x = np.array(
        [
            initial_state.x,
            initial_state.y,
            initial_state.z,
            initial_state.vx,
            initial_state.vy,
            0.0,
            0.0,
        ]
    )
    accel_measurements = _simulate_acceleration_measurements(
        reference_trajectory,
        signal.timeline,
        accel_noise_variance,
    )

    result = []
    total_steps = len(signal.timeline)
    for i in range(total_steps):
        if is_cancelled and is_cancelled():
            raise CancelledError()

        t = signal.timeline[i]
        dt = float(t - signal.timeline[i - 1]) if i > 0 else 0.0
        ax, ay = accel_measurements[i] if i < len(accel_measurements) else (0.0, 0.0)

        q = process_noise_std**2
        Q = np.zeros((n_states, n_states))
        if dt > 0.0:
            dt2 = dt * dt
            dt3 = dt2 * dt
            dt4 = dt2 * dt2
            dt5 = dt4 * dt
            Q[0, 0] = dt5 / 20.0 * q
            Q[1, 1] = dt5 / 20.0 * q
            Q[3, 3] = dt3 / 3.0 * q
            Q[4, 4] = dt3 / 3.0 * q
            Q[5, 5] = dt * q
            Q[6, 6] = dt * q
            cross_pos_vel = dt4 / 8.0 * q
            cross_pos_acc = dt3 / 6.0 * q
            cross_vel_acc = dt2 / 2.0 * q
            Q[0, 3] = Q[3, 0] = cross_pos_vel
            Q[1, 4] = Q[4, 1] = cross_pos_vel
            Q[0, 5] = Q[5, 0] = cross_pos_acc
            Q[1, 6] = Q[6, 1] = cross_pos_acc
            Q[3, 5] = Q[5, 3] = cross_vel_acc
            Q[4, 6] = Q[6, 4] = cross_vel_acc
        Q[2, 2] = max(1e-9, 0.01 * q)

        def f_state(state, _dt=dt):
            return np.array(
                [
                    state[0] + _dt * state[3] + 0.5 * state[5] * _dt * _dt,
                    state[1] + _dt * state[4] + 0.5 * state[6] * _dt * _dt,
                    state[2],
                    state[3] + state[5] * _dt,
                    state[4] + state[6] * _dt,
                    state[5],
                    state[6],
                ]
            )

        meas = signal.measurements[i]
        selected = _select_valid_rss_indices(meas.values, min_rss_threshold)
        bp = beacon_positions[selected]
        if use_walls:
            w_att = _compute_wall_attenuation(
                x,
                bp,
                wall_list,
                door_list,
                wall_attenuation_db,
            )
        else:
            w_att = None

        z = np.concatenate((meas.values[selected], np.array([ax, ay])))
        rss_R = measurement_noise_std**2 * np.eye(len(selected))
        R = np.block(
            [
                [rss_R, np.zeros((len(selected), 2))],
                [np.zeros((2, len(selected))), accel_R],
            ]
        )
        h_meas = (
            lambda state, bp=bp, a=rssi_at_ref, n=path_loss_exponent, _d0=d0, wa=w_att: (
                np.concatenate(
                    (
                        _u2rss(state, bp, a, n, _d0, wa),
                        state[5:7],
                    )
                )
            )
        )
        x, P = _ekf_update(
            f_state,
            x,
            P,
            h_meas,
            z,
            Q,
            R,
            max_normalized_innovation=9.0 * len(z),
        )
        result.append(
            TrajectoryPoint(
                x=float(x[0]),
                y=float(x[1]),
                z=float(initial_state.z),
                t=float(t),
            )
        )

        if progress_callback:
            progress_callback(i + 1, total_steps)

    return result


# ── Unscented Kalman Filter (matches pos2D_UKF_RSS.m) ──


def _sigma_points(x: np.ndarray, P: np.ndarray, c: float) -> np.ndarray:
    A = c * cholesky(P)
    n = len(x)
    X = np.zeros((n, 2 * n + 1))
    X[:, 0] = x
    for i in range(n):
        X[:, i + 1] = x + A[:, i]
        X[:, n + i + 1] = x - A[:, i]
    return X


def _unscented_transform(
    fn,
    X: np.ndarray,
    Wm: np.ndarray,
    Wc: np.ndarray,
    n_out: int,
    R: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    L = X.shape[1]
    Y = np.zeros((n_out, L))
    for k in range(L):
        Y[:, k] = fn(X[:, k])

    y = Y @ Wm
    Y1 = Y - y[:, np.newaxis]
    P = Y1 @ np.diag(Wc) @ Y1.T + R
    return y, Y, P, Y1


def _ukf_update(
    f_state,
    x: np.ndarray,
    P: np.ndarray,
    h_meas,
    z: np.ndarray,
    Q: np.ndarray,
    R: np.ndarray,
    max_normalized_innovation: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    L = len(x)
    m = len(z)
    alpha = 1e-3
    kappa = 0.0
    beta = 2.0
    lam = alpha**2 * (L + kappa) - L
    c = L + lam

    Wm = np.full(2 * L + 1, 0.5 / c)
    Wm[0] = lam / c
    Wc = Wm.copy()
    Wc[0] = Wc[0] + (1 - alpha**2 + beta)

    c_sqrt = np.sqrt(c)

    P = 0.5 * (P + P.T)
    P += 1e-6 * np.eye(L)

    try:
        X = _sigma_points(x, P, c_sqrt)
    except np.linalg.LinAlgError:
        return x, P

    x1, X1, P1, X2 = _unscented_transform(f_state, X, Wm, Wc, L, Q)
    z1, Z1, P2, Z2 = _unscented_transform(h_meas, X1, Wm, Wc, m, R)

    P12 = X2 @ np.diag(Wc) @ Z2.T

    P2 = 0.5 * (P2 + P2.T)
    P2 += 1e-6 * np.eye(P2.shape[0])

    try:
        K = P12 @ np.linalg.inv(P2)
    except np.linalg.LinAlgError:
        return x1, P1

    if max_normalized_innovation is not None:
        innovation = z - z1
        try:
            nis = float(innovation.T @ np.linalg.solve(P2, innovation))
        except np.linalg.LinAlgError:
            return x1, P1
        if nis > max_normalized_innovation:
            return x1, P1

    x = x1 + K @ (z - z1)
    P = P1 - K @ P12.T
    P = 0.5 * (P + P.T)
    return x, P


def estimate_ukf_rss(
    signal: BeaconSignal,
    initial_state: TrajectoryPoint,
    process_noise_std: float = 1.0,
    measurement_noise_std: float = 2.0,
    path_loss_exponent: float = 2.0,
    walls: list[Wall] | None = None,
    doors: list[Door] | None = None,
    wall_attenuation_db: float = 0.0,
    progress_callback: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    rssi_at_ref: float = -59.0,
    d0: float = 1.0,
    min_rss_threshold: float | None = None,
) -> list[TrajectoryPoint]:
    n_states = 5

    beacon_positions = np.array([b.r for b in signal.beacons])

    wall_list = walls or []
    door_list = doors or []
    use_walls = wall_attenuation_db > 0 and bool(wall_list)

    P = np.diag([10.0, 10.0, 1.0, 2.0, 2.0])

    x = np.array(
        [
            initial_state.x,
            initial_state.y,
            initial_state.z,
            initial_state.vx,
            initial_state.vy,
        ]
    )

    result = []
    total_steps = len(signal.timeline)
    for i in range(total_steps):
        if is_cancelled and is_cancelled():
            raise CancelledError()

        t = signal.timeline[i]
        dt = float(t - signal.timeline[i - 1]) if i > 0 else 1.0 / signal.frequency

        Q = _build_cv_process_noise(dt, n_states, process_noise_std)

        def f_state(state, _dt=dt):
            return np.array(
                [
                    state[0] + _dt * state[3],
                    state[1] + _dt * state[4],
                    state[2],
                    state[3],
                    state[4],
                ]
            )

        meas = signal.measurements[i]
        selected = _select_valid_rss_indices(meas.values, min_rss_threshold)
        if len(selected) == 0:
            x = f_state(x)
            P = P + Q
        else:
            bp = beacon_positions[selected]
            z = meas.values[selected]
            if use_walls:
                w_att = _compute_wall_attenuation(
                    x,
                    bp,
                    wall_list,
                    door_list,
                    wall_attenuation_db,
                )
            else:
                w_att = None
            h_meas = (
                lambda state, _bp=bp, a=rssi_at_ref, n=path_loss_exponent, _d0=d0, wa=w_att: (
                    _u2rss(state, _bp, a, n, _d0, wa)
                )
            )
            R = measurement_noise_std**2 * np.eye(len(selected))
            x, P = _ukf_update(
                f_state,
                x,
                P,
                h_meas,
                z,
                Q,
                R,
                max_normalized_innovation=9.0 * len(selected),
            )
        result.append(
            TrajectoryPoint(
                x=float(x[0]),
                y=float(x[1]),
                z=float(initial_state.z),
                t=float(t),
            )
        )

        if progress_callback:
            progress_callback(i + 1, total_steps)

    return result


# ── Trilateration with ToF (matches pos2D_Tri_ToF.m) ──


def _trilaterate(
    beacon_positions: np.ndarray,
    distances: np.ndarray,
    known_z: float,
) -> np.ndarray:
    horizontal_sq = distances**2 - (beacon_positions[2, :] - known_z) ** 2
    valid_mask = np.isfinite(horizontal_sq) & (horizontal_sq >= 0.0)

    beacon_positions = beacon_positions[:, valid_mask]
    horizontal_distances = np.sqrt(horizontal_sq[valid_mask])
    n_beacons = beacon_positions.shape[1]

    if n_beacons < 3:
        raise np.linalg.LinAlgError("Not enough valid beacons for 2D trilateration")

    A = np.zeros((n_beacons, 3))
    b = np.zeros(n_beacons)
    for i in range(n_beacons):
        x_b, y_b, _z_b = beacon_positions[:, i]
        s = horizontal_distances[i]
        A[i] = [1, -2 * x_b, -2 * y_b]
        b[i] = s**2 - x_b**2 - y_b**2

    result = np.linalg.lstsq(A, b, rcond=None)[0]
    return result[1:3]


def estimate_trilateration_tof(
    signal: BeaconSignal,
    initial_state: TrajectoryPoint,
    progress_callback: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[TrajectoryPoint]:
    beacon_positions = np.array([b.r for b in signal.beacons]).T

    x = np.array([initial_state.x, initial_state.y])

    result = []
    total_steps = len(signal.timeline)
    for i, t in enumerate(signal.timeline):
        if is_cancelled and is_cancelled():
            raise CancelledError()

        meas = signal.measurements[i]
        distances = SPEED_OF_LIGHT * meas.values

        valid_mask = np.isfinite(distances) & (distances > 0)
        n_valid = int(np.sum(valid_mask))

        if n_valid >= 3:
            try:
                x = _trilaterate(
                    beacon_positions[:, valid_mask],
                    distances[valid_mask],
                    initial_state.z,
                )
            except np.linalg.LinAlgError:
                pass

        result.append(
            TrajectoryPoint(
                x=float(x[0]),
                y=float(x[1]),
                z=float(initial_state.z),
                t=float(t),
            )
        )

        if progress_callback:
            progress_callback(i + 1, total_steps)

    return result


def _rss_to_distance(
    rss: np.ndarray,
    rssi_at_ref: float,
    path_loss_exponent: float,
    d0: float = 1.0,
    wall_attenuation: np.ndarray | None = None,
) -> np.ndarray:
    effective_rss = rss.copy()
    if wall_attenuation is not None:
        effective_rss = effective_rss + wall_attenuation
    exponent = (rssi_at_ref - effective_rss) / (10.0 * path_loss_exponent)
    return d0 * np.power(10.0, exponent)


def estimate_trilateration_rss(
    signal: BeaconSignal,
    initial_state: TrajectoryPoint,
    path_loss_exponent: float = 2.0,
    walls: list[Wall] | None = None,
    doors: list[Door] | None = None,
    wall_attenuation_db: float = 0.0,
    progress_callback: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    rssi_at_ref: float = -59.0,
    d0: float = 1.0,
    min_rss_threshold: float | None = None,
) -> list[TrajectoryPoint]:
    all_beacon_positions = np.array([b.r for b in signal.beacons]).T

    wall_list = walls or []
    door_list = doors or []
    use_walls = wall_attenuation_db > 0 and bool(wall_list)

    x = np.array([initial_state.x, initial_state.y])

    result = []
    total_steps = len(signal.timeline)
    for i, t in enumerate(signal.timeline):
        if is_cancelled and is_cancelled():
            raise CancelledError()

        meas = signal.measurements[i]
        rss_values = meas.values

        if use_walls:
            pos_3d = np.array([x[0], x[1], initial_state.z])
            w_att = np.zeros(len(signal.beacons))
            for j in range(len(signal.beacons)):
                n_walls = count_wall_crossings(
                    pos_3d, all_beacon_positions[:, j], wall_list, door_list
                )
                w_att[j] = n_walls * wall_attenuation_db
        else:
            w_att = None

        distances = _rss_to_distance(
            rss_values, rssi_at_ref, path_loss_exponent, d0, w_att
        )

        valid_mask = np.isfinite(distances) & (distances > 0) & np.isfinite(rss_values)
        if min_rss_threshold is not None:
            valid_mask &= rss_values >= min_rss_threshold
        valid_indices = np.where(valid_mask)[0]

        if len(valid_indices) >= 3:
            selected = valid_indices[np.argsort(rss_values[valid_indices])[::-1][:3]]

            try:
                x = _trilaterate(
                    all_beacon_positions[:, selected],
                    distances[selected],
                    initial_state.z,
                )
            except np.linalg.LinAlgError:
                pass

        result.append(
            TrajectoryPoint(
                x=float(x[0]),
                y=float(x[1]),
                z=float(initial_state.z),
                t=float(t),
            )
        )

        if progress_callback:
            progress_callback(i + 1, total_steps)

    return result


SPEED_OF_LIGHT = 3e8

TRACKING_ALGORITHMS = {
    "pos2D_EKF_RSS": {
        "fn": estimate_ekf_rss,
        "signal_type": SignalType.RSS,
        "label": "EKF + RSS",
    },
    "pos2D_EKF_ToF": {
        "fn": estimate_ekf_tof,
        "signal_type": SignalType.TOF,
        "label": "EKF + ToF",
    },
    "pos2D_EKF_RSS_Accel": {
        "fn": estimate_ekf_rss_accel,
        "signal_type": SignalType.RSS,
        "label": "EKF + RSS + Accel",
    },
    "pos2D_UKF_RSS": {
        "fn": estimate_ukf_rss,
        "signal_type": SignalType.RSS,
        "label": "UKF + RSS",
    },
    "pos2D_Tri_ToF": {
        "fn": estimate_trilateration_tof,
        "signal_type": SignalType.TOF,
        "label": "Trilateration + ToF",
    },
    "pos2D_Tri_RSS": {
        "fn": estimate_trilateration_rss,
        "signal_type": SignalType.RSS,
        "label": "Trilateration + RSS",
    },
}
