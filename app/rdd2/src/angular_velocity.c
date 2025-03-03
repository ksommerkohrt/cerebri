/*
 * Copyright CogniPilot Foundation 2023
 * SPDX-License-Identifier: Apache-2.0
 */

#include <assert.h>

#include <synapse_topic_list.h>
#include <zephyr/logging/log.h>
#include <zephyr/shell/shell.h>

#include <zros/private/zros_node_struct.h>
#include <zros/private/zros_pub_struct.h>
#include <zros/private/zros_sub_struct.h>
#include <zros/zros_node.h>
#include <zros/zros_pub.h>
#include <zros/zros_sub.h>

#include <cerebri/core/casadi.h>

#include "casadi/gen/rdd2.h"

#define MY_STACK_SIZE 3072
#define MY_PRIORITY 4

LOG_MODULE_REGISTER(rdd2_angular_velocity, CONFIG_CEREBRI_RDD2_LOG_LEVEL);

static K_THREAD_STACK_DEFINE(g_my_stack_area, MY_STACK_SIZE);

struct context {
    struct zros_node node;
    synapse_msgs_Status status;
    synapse_msgs_Vector3 angular_velocity_sp, moment_sp;
    synapse_msgs_Odometry estimator_odometry;
    struct zros_sub sub_status, sub_angular_velocity_sp, sub_estimator_odometry;
    struct zros_pub pub_moment_sp;
    atomic_t running;
    size_t stack_size;
    k_thread_stack_t* stack_area;
    struct k_thread thread_data;
};

static struct context g_ctx = {
    .node = {},
    .status = synapse_msgs_Status_init_default,
    .moment_sp = synapse_msgs_Vector3_init_default,
    .angular_velocity_sp = synapse_msgs_Vector3_init_default,
    .sub_status = {},
    .sub_angular_velocity_sp = {},
    .sub_estimator_odometry = {},
    .pub_moment_sp = {},
    .running = ATOMIC_INIT(0),
    .stack_size = MY_STACK_SIZE,
    .stack_area = g_my_stack_area,
    .thread_data = {},
};

static void rdd2_angular_velocity_init(struct context* ctx)
{
    LOG_INF("init");
    zros_node_init(&ctx->node, "rdd2_angular_velocity");
    zros_sub_init(&ctx->sub_status, &ctx->node, &topic_status, &ctx->status, 10);
    zros_sub_init(&ctx->sub_angular_velocity_sp, &ctx->node,
        &topic_angular_velocity_sp, &ctx->angular_velocity_sp, 300);
    zros_sub_init(&ctx->sub_estimator_odometry, &ctx->node,
        &topic_estimator_odometry, &ctx->estimator_odometry, 300);
    zros_pub_init(&ctx->pub_moment_sp, &ctx->node, &topic_moment_sp, &ctx->moment_sp);
    atomic_set(&ctx->running, 1);
}

static void rdd2_angular_velocity_fini(struct context* ctx)
{
    LOG_INF("fini");
    zros_node_fini(&ctx->node);
    zros_sub_fini(&ctx->sub_status);
    zros_sub_fini(&ctx->sub_angular_velocity_sp);
    zros_sub_fini(&ctx->sub_estimator_odometry);
    zros_pub_fini(&ctx->pub_moment_sp);
    atomic_set(&ctx->running, 0);
}

static void rdd2_angular_velocity_run(void* p0, void* p1, void* p2)
{
    struct context* ctx = p0;
    ARG_UNUSED(p1);
    ARG_UNUSED(p2);

    rdd2_angular_velocity_init(ctx);

    struct k_poll_event events[] = {
        *zros_sub_get_event(&ctx->sub_estimator_odometry),
    };

    double dt = 0;
    int64_t ticks_last = k_uptime_ticks();

    // angular velocity integrator states
    double omega_i[3] = { 0, 0, 0 };

    while (atomic_get(&ctx->running)) {
        // wait for estimator odometry, publish at 10 Hz regardless
        int rc = 0;
        rc = k_poll(events, ARRAY_SIZE(events), K_MSEC(100));
        if (rc != 0) {
            LOG_DBG("not receiving estimator odometry");
        }

        if (zros_sub_update_available(&ctx->sub_status)) {
            zros_sub_update(&ctx->sub_status);
        }

        if (zros_sub_update_available(&ctx->sub_estimator_odometry)) {
            zros_sub_update(&ctx->sub_estimator_odometry);
        }

        if (zros_sub_update_available(&ctx->sub_angular_velocity_sp)) {
            zros_sub_update(&ctx->sub_angular_velocity_sp);
        }

        // calculate dt
        int64_t ticks_now = k_uptime_ticks();
        dt = (double)(ticks_now - ticks_last) / CONFIG_SYS_CLOCK_TICKS_PER_SEC;
        ticks_last = ticks_now;
        if (dt < 0 || dt > 0.1) {
            LOG_DBG("odometry rate too low");
            continue;
        }

        {
            /* attitude_rate_control:
             * (omega[3],omega_r[3],omega_i[3],dt)
             * ->(M[3],omega_i_update[3]) */
            CASADI_FUNC_ARGS(attitude_rate_control);
            double omega[3];
            double omega_r[3];
            double M[3];
            omega[0] = ctx->estimator_odometry.twist.twist.angular.x;
            omega[1] = ctx->estimator_odometry.twist.twist.angular.y;
            omega[2] = ctx->estimator_odometry.twist.twist.angular.z;

            omega_r[0] = ctx->angular_velocity_sp.x;
            omega_r[1] = ctx->angular_velocity_sp.y;
            omega_r[2] = ctx->angular_velocity_sp.z;

            args[0] = omega;
            args[1] = omega_r;
            args[2] = omega_i;
            args[3] = &dt;
            res[0] = M;
            res[1] = omega_i;
            CASADI_FUNC_CALL(attitude_rate_control);

            LOG_DBG("omega_i: %10.4f %10.4f %10.4f",
                omega_i[0], omega_i[1], omega_i[2]);

            // compute control
            ctx->moment_sp.x = M[0];
            ctx->moment_sp.y = M[1];
            ctx->moment_sp.z = M[2];
        }

        // publish
        zros_pub_update(&ctx->pub_moment_sp);
    }

    rdd2_angular_velocity_fini(ctx);
}

static int start(struct context* ctx)
{
    k_tid_t tid = k_thread_create(&ctx->thread_data, ctx->stack_area,
        ctx->stack_size,
        rdd2_angular_velocity_run,
        ctx, NULL, NULL,
        MY_PRIORITY, 0, K_FOREVER);
    k_thread_name_set(tid, "rdd2_angular_velocity");
    k_thread_start(tid);
    return 0;
}

static int rdd2_angular_velocity_cmd_handler(const struct shell* sh,
    size_t argc, char** argv, void* data)
{
    struct context* ctx = data;
    assert(argc == 1);

    if (strcmp(argv[0], "start") == 0) {
        if (atomic_get(&ctx->running)) {
            shell_print(sh, "already running");
        } else {
            start(ctx);
        }
    } else if (strcmp(argv[0], "stop") == 0) {
        if (atomic_get(&ctx->running)) {
            atomic_set(&ctx->running, 0);
        } else {
            shell_print(sh, "not running");
        }
    } else if (strcmp(argv[0], "status") == 0) {
        shell_print(sh, "running: %d", (int)atomic_get(&ctx->running));
    }
    return 0;
}

SHELL_SUBCMD_DICT_SET_CREATE(sub_rdd2_angular_velocity, rdd2_angular_velocity_cmd_handler,
    (start, &g_ctx, "start"),
    (stop, &g_ctx, "stop"),
    (status, &g_ctx, "status"));

SHELL_CMD_REGISTER(rdd2_angular_velocity, &sub_rdd2_angular_velocity, "rdd2 angular velocity commands", NULL);

static int rdd2_angular_velocity_sys_init(void)
{
    return start(&g_ctx);
};

SYS_INIT(rdd2_angular_velocity_sys_init, APPLICATION, 2);

// vi: ts=4 sw=4 et
