#!/usr/bin/env python3
import os
import sys
import math
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import casadi as ca
import cyecca.lie as lie
from cyecca.lie.group_so3 import SO3Quat, SO3EulerB321

# parameters
thrust_delta = 0.1 # thrust delta from trim
thrust_trim = 0.5 # thrust trim
deg2rad = np.pi/180 # degree to radian
g = 9.8 # grav accel m/s^2
m = 2.0 # mass of vehicle

# position loop
kp_pos = 0.2 # position proportional gain
kp_vel = 1 # velocity proportional gain

# attitude loop
kp_rollpitch = 2
kp_yaw = 2
yaw_rate_max = 60 # deg/s
rollpitch_max = 20 # deg

# attittude rate loop
rollpitch_rate_max = 60 # deg/s
kp_rollpitch_rate = 0.015
ki_rollpitch_rate = 0.05
kp_yaw_rate = 0.1
ki_yaw_rate = 0.02

pos_sp_dist_max = 2 # position setpoint max distance
vel_max = 2.0 # max velocity command

rollpitch_rate_integral_max = 1.0
yaw_rate_integral_max = 1.0


def saturate(x, x_min, x_max):
    """
    saturate a vector
    """
    y = x
    for i in range(x.shape[0]):
        y[i] =  ca.if_else(x[i] > x_max[i], x_max[i], ca.if_else(x[i] < x_min[i], x_min[i], x[i]))
    return y

def derive_joy_acro():
    """
    Acro mode manual input:

    Given joy input, find roll rate and thrust setpoints
    """

    # INPUTS
    # -------------------------------
    joy_roll = ca.SX.sym('joy_roll')
    joy_pitch = ca.SX.sym('joy_pitch')
    joy_yaw = ca.SX.sym('joy_yaw')
    joy_thrust = ca.SX.sym('joy_thrust')

    # CALC
    # -------------------------------
    w = ca.vertcat(
        -rollpitch_rate_max*deg2rad*joy_roll,
        rollpitch_rate_max*deg2rad*joy_pitch,
        yaw_rate_max*deg2rad*joy_yaw)
    thrust = joy_thrust * thrust_delta + thrust_trim

    # FUNCTION
    # -------------------------------
    f_joy_acro = ca.Function(
       "joy_acro",
       [joy_roll, joy_pitch, joy_yaw, joy_thrust],
       [w, thrust],
       ["joy_roll", "joy_pitch", "joy_yaw", "joy_thrust"],
       ["omega", "thrust"])

    return {
        "joy_acro": f_joy_acro
    }


def derive_joy_auto_level():
    """
    Auto level mode manual input:

    Given joy input, find attitude and thrust set points
    """

    # INPUTS
    # -------------------------------
    joy_roll = ca.SX.sym('joy_roll')
    joy_pitch = ca.SX.sym('joy_pitch')
    joy_yaw = ca.SX.sym('joy_yaw')
    joy_thrust = ca.SX.sym('joy_thrust')

    q = SO3Quat.elem(ca.SX.sym('q', 4))

    # CALC
    # -------------------------------
    euler = SO3EulerB321.from_Quat(q)
    yaw = euler.param[0]
    pitch = euler.param[1]
    roll = euler.param[2]

    euler_r = SO3EulerB321.elem(ca.vertcat(
        yaw + yaw_rate_max * deg2rad * joy_yaw,
        rollpitch_max * deg2rad * joy_pitch,
        -rollpitch_max * deg2rad * joy_roll))

    q_r = SO3Quat.from_Euler(euler_r)
    thrust = joy_thrust * thrust_delta + thrust_trim

    # FUNCTION
    # -------------------------------
    f_joy_auto_level = ca.Function(
       "joy_auto_level",
       [joy_roll, joy_pitch, joy_yaw, joy_thrust, q.param],
       [q_r.param, thrust],
       ["joy_roll", "joy_pitch", "joy_yaw", "joy_thrust", "q"],
       ["q_r", "thrust"])

    return {
        "joy_auto_level": f_joy_auto_level
    }


def derive_joy_position():
    """
    Position mode manual input:

    Given joy input, find position and velocity set points
    """

    # INPUTS
    # -------------------------------

    # joy, -1 to 1
    joy_roll = ca.SX.sym('joy_roll')
    joy_pitch = ca.SX.sym('joy_pitch')
    joy_yaw = ca.SX.sym('joy_yaw')
    joy_thrust = ca.SX.sym('joy_thrust')

    p = ca.SX.sym('p', 3)  # position
    p_r = ca.SX.sym('p_r', 3)  # position set point

    # CALC
    # -------------------------------

    # attitude
    q = SO3Quat.elem(ca.SX.sym('q', 4))
    euler = SO3EulerB321.from_Quat(q)
    yaw = euler.param[0]
    pitch = euler.param[1]
    roll = euler.param[2]

    yaw_rate = 60 * deg2rad * joy_yaw

    # velocity in body frame
    vbx = vel_max * joy_pitch
    vby = vel_max * joy_roll

    # velocity in world frame
    vwx = vbx * ca.cos(yaw) - vby * ca.sin(yaw)
    vwy = vbx * ca.sin(yaw) + vby * ca.cos(yaw)
    vwz = joy_thrust

    p_e = p_r - p  # position error
    norm_e = ca.norm_2(p_e)
    #ca.if_else(norm_e > pos_eror_max, 

    euler_r = SO3EulerB321.elem(ca.vertcat(
        yaw + yaw_rate_max * joy_yaw,
        rollpitch_max * joy_pitch,
        -rollpitch_max * joy_roll))

    q_r = SO3Quat.from_Euler(euler_r)
    thrust = joy_thrust * thrust_delta + thrust_trim

    # FUNCTION
    # -------------------------------
    f_joy_position = ca.Function(
       "joy_position",
       [joy_roll, joy_pitch, joy_yaw, joy_thrust, q.param],
       [q_r.param, thrust],
       ["joy_roll", "joy_pitch", "joy_yaw", "joy_thrust", "q"],
       ["q_r", "thrust"])

    return {
        "joy_position": f_joy_position
    }


def derive_quat_to_eulerB321():
    """
    quaternion to eulerB321 converion
    """

    # INPUTS
    # -------------------------------
    q_wb = ca.SX.sym('q', 4)
    X = SO3Quat.elem(q_wb)
    e = SO3EulerB321.from_Quat(X)

    # FUNCTION
    # -------------------------------
    f_quat_to_eulerB321 = ca.Function(
       "quat_to_eulerB321",
       [q_wb], [e.param[0], e.param[1], e.param[2]],
       ["q_wb"], ["yaw", "pitch", "roll"])

    return {
        "quat_to_eulerB321": f_quat_to_eulerB321
    }

def derive_eulerB321_to_quat():
    """
    eulerB321 to quaternion converion
    """

    # INPUTS
    # -------------------------------
    e = SO3EulerB321.elem(ca.SX.sym('e', 3))

    # CALC
    # -------------------------------
    X = SO3Quat.from_Euler(e)

    # FUNCTION
    # -------------------------------
    f_eulerB321_to_quat = ca.Function(
       "eulerB321_to_quat",
       [e.param[0], e.param[1], e.param[2]], [X.param],
       ["yaw", "pitch", "roll"], ["q"])

    return {
        "eulerB321_to_quat": f_eulerB321_to_quat
    }


def derive_attitude_control():
    """
    Attitude control loop

    Given desired attitude, and attitude, find desired angular velocity
    """

    # INPUT
    # -------------------------------
    q = ca.SX.sym('q', 4) # actual quat
    q_r = ca.SX.sym('q_r', 4) # quat setpoint

    # CALC
    # -------------------------------
    kp = ca.vertcat(kp_rollpitch, kp_rollpitch, kp_yaw)

    X = SO3Quat.elem(q)

    X_r = SO3Quat.elem(q_r)

    # Lie algebra
    e = (X.inverse() * X_r).log()  # angular velocity to get to desired att in 1 sec

    omega = kp * e.param # elementwise

    # FUNCTION
    # -------------------------------
    f_attitude_control = ca.Function(
        "attitude_control",
        [q, q_r],
        [omega],
        ["q", "q_r"],
        ["omega"])

    return {
        "attitude_control": f_attitude_control
    }


def derive_attitude_rate_control():
    """
    Attitude rate control loop

    Given angular velocity , angular vel. set point, and angular velocity error integral,
    find the desired moment and updated angular velocity error integral.
    """

    # INPUT
    # -------------------------------
    omega = ca.SX.sym('omega', 3)
    omega_r = ca.SX.sym('omega_r', 3)
    omega_i = ca.SX.sym('omega_i', 3)
    dt = ca.SX.sym('dt')

    # CALC
    # -------------------------------
    kp = ca.vertcat(kp_rollpitch_rate, kp_rollpitch_rate, kp_yaw_rate)

    # actual attitude, expressed as quaternion
    omega_e = omega_r - omega

    # integral action helps balance distrubance moments (e.g. center of gravity offset)
    ki = ca.vertcat(ki_rollpitch_rate, ki_rollpitch_rate, ki_yaw_rate)
    omega_i_2 = omega_i + omega_e * dt
    integral_max = ca.vertcat(rollpitch_rate_integral_max,
            rollpitch_rate_integral_max, yaw_rate_integral_max)
    omega_i_2 = saturate(omega_i_2, -integral_max, integral_max)

    M = kp * omega_e + ki * omega_i_2

    # FUNCTION
    # -------------------------------
    f_attitude_rate_control = ca.Function(
        "attitude_rate_control",
        [omega, omega_r, omega_i, dt],
        [M, omega_i_2],
        ["omega", "omega_r", "omega_i", "dt"],
        ["M", "omega_i_update"])

    return {
        "attitude_rate_control": f_attitude_rate_control
    }


def derive_position_control():
    """
    Given the position, velocity ,and acceleration set points, find the
    desired attitude and thrust.
    """

    # INPUT
    # -------------------------------

    #inputs: position trajectory, velocity trajectory, desired Yaw vel, dt
    #state inputs: position, orientation, velocity, and angular velocity
    #outputs: thrust force, angular errors
    pt_w = ca.SX.sym('pt_w', 3) # desired position world frame
    vt_w = ca.SX.sym('vt_w', 3) # desired velocity world frame
    at_w = ca.SX.sym('at_w', 3) # desired acceleration world frame

    qc_wb = SO3Quat.elem(ca.SX.sym('qc_wb', 4)) # camera orientation
    p_w = ca.SX.sym('p_w', 3) # position in world frame
    v_b = ca.SX.sym('v_b', 3) # velocity in body frame
    q_wb = SO3Quat.elem(ca.SX.sym('q_wb', 4))

    # CALC
    # -------------------------------
    R_wb = q_wb.to_Matrix()
    
    v_w = R_wb @ v_b

    e_p = p_w - pt_w
    e_v = v_w - vt_w

    xW = ca.SX([1, 0, 0])
    yW = ca.SX([0, 1, 0])
    zW = ca.SX([0, 0, 1])

    # F = - Kp ep - Kv ev + mg zW + m at_w
    # F = - m * Kp' ep - m * Kv' * ev + mg zW + m at_w
    # Force is normalized by the weight (mg)

    # normalized thrust vector, normalized by twice weight
    p_norm_max = 0.3
    p_term = -kp_pos * e_p / (2*g) - kp_vel * e_v / (2*g) + at_w / (2*g)
    p_norm = ca.norm_2(p_term)
    p_term = ca.if_else(p_norm > p_norm_max, p_norm_max*p_term/p_norm, p_term)

    # trim throttle
    T0 = zW / 2

    T = p_term + T0

    # thrust
    nT = ca.norm_2(T)
    
    # body up is aligned with thrust
    zB = ca.if_else(nT > 1e-3, T/nT, zW)

    # point y using desired camera direction
    ec = SO3EulerB321.from_Quat(qc_wb)
    yt = ec.param[0]
    xC = ca.vertcat(ca.cos(yt), ca.sin(yt), 0)
    yB = ca.cross(zB, xC)
    nyB = ca.norm_2(yB)
    yB = ca.if_else(nyB > 1e-3, yB/nyB, xW)

    # point x using cross product of unit vectors
    xB = ca.cross(yB, zB)

    # desired attitude matrix
    Rd_wb = ca.horzcat(xB, yB, zB)
    # [bx_wx by_wx bz_wx]
    # [bx_wy by_wy bz_wy]
    # [bx_wz by_wz bz_wz]

    # deisred euler angles
    # note using euler angles as set point is not problematic
    # using Lie group approach for control
    qr_wb = SO3Quat.from_Matrix(Rd_wb)

    # FUNCTION
    # -------------------------------
    f_get_u = ca.Function(
        "position_control",
        [pt_w, vt_w, at_w, qc_wb.param, p_w, v_b, q_wb.param], [nT, qr_wb.param], 
        ['pt_w', 'vt_w', 'at_w', 'qc_wb', 'p_w', 'v_b', 'q_wb'], 
        ['nT', 'qr_wb'])
    
    return {
        "position_control" : f_get_u
    }


def generate_code(eqs: dict, filename, dest_dir: str, **kwargs):
    """
    Generate C Code from python CasADi functions.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(exist_ok=True)
    p = {
        "verbose": True,
        "mex": False,
        "cpp": False,
        "main": False,
        "with_header": True,
        "with_mem": False,
        "with_export": False,
        "with_import": False,
        "include_math": True,
        "avoid_stack": True,
    }
    for k, v in kwargs.items():
        assert k in p.keys()
        p[k] = v

    gen = ca.CodeGenerator(filename, p)
    for name, eq in eqs.items():
        gen.add(eq)
    gen.generate(str(dest_dir) + os.sep)

if __name__ == "__main__":
    print("generating casadi equations")
    eqs = {}
    eqs.update(derive_attitude_rate_control())
    eqs.update(derive_attitude_control())
    eqs.update(derive_position_control())
    eqs.update(derive_eulerB321_to_quat())
    eqs.update(derive_quat_to_eulerB321())
    eqs.update(derive_joy_acro())
    eqs.update(derive_joy_auto_level())
    eqs.update(derive_joy_position())

    for name, eq in eqs.items():
        print('eq: ', name)

    generate_code(eqs, filename="rdd2.c", dest_dir="gen")
    print("complete")
