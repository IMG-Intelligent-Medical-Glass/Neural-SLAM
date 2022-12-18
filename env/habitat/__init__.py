# Parts of the code in this file have been borrowed from:
#    https://github.com/facebookresearch/habitat-api

import numpy as np
import torch

from .exploration_env import Exploration_Env
from .habitat_api.habitat.core.vector_env import VectorEnv
from .habitat_api.habitat_baselines.config.default import get_config as cfg_baseline


from habitat_api.habitat.datasets.pointnav.pointnav_dataset import  PointNavDatasetV1
from habitat_api.habitat.config.default import get_config as cfg_env
# from habitat.datasets.pointnav.pointnav_dataset import PointNavDatasetV1
# from habitat.config.default import get_config as cfg_env


def make_env_fn(args, config_env, config_baseline, rank):
    dataset = PointNavDatasetV1(config_env.DATASET)
    config_env.defrost()
    config_env.SIMULATOR.SCENE = dataset.episodes[0].scene_id
    print("Loading {}".format(config_env.SIMULATOR.SCENE))
    config_env.freeze()

    env = Exploration_Env(args=args, rank=rank,
                          config_env=config_env, config_baseline=config_baseline, dataset=dataset
                          )

    env.seed(rank)
    return env


def construct_envs(args):
    env_configs = []
    baseline_configs = []
    args_list = []

    # args.task_config = tasks/pointnav_gibson.yaml
    # habitat.api
    basic_config = cfg_env(config_paths=
                           ["env/habitat/habitat_api/configs/" + args.task_config])
    basic_config.defrost()
    basic_config.DATASET.SPLIT = args.split
    basic_config.freeze()

    # habitat.api
    scenes = PointNavDatasetV1.get_scenes_to_load(basic_config.DATASET)

    if len(scenes) > 0:
        # 进程过多，场景数量不够，减少一些进程
        assert len(scenes) >= args.num_processes, (
            "reduce the number of processes as there "
            "aren't enough number of scenes"
        )
        scene_split_size = int(np.floor(len(scenes) / args.num_processes))

    for i in range(args.num_processes):
        config_env = cfg_env(config_paths=
                             ["env/habitat/habitat_api/configs/" + args.task_config])
        config_env.defrost()

        if len(scenes) > 0:
            config_env.DATASET.CONTENT_SCENES = scenes[
                                                i * scene_split_size: (i + 1) * scene_split_size
                                                ]

        # 第一个gpu的进程数量
        if i < args.num_processes_on_first_gpu:
            gpu_id = 0

        # 剩余的gpu进程数量，每当i大于一个num_processes_per_gpu的时候gpu_id+1
        else:
            gpu_id = int((i - args.num_processes_on_first_gpu)
                         // args.num_processes_per_gpu) + args.sim_gpu_id


        gpu_id = min(torch.cuda.device_count() - 1, gpu_id)

        # 设置当前环境所用的gpu
        config_env.SIMULATOR.HABITAT_SIM_V0.GPU_DEVICE_ID = gpu_id


        # 添加传感器
        agent_sensors = []
        agent_sensors.append("RGB_SENSOR")
        agent_sensors.append("DEPTH_SENSOR")
        config_env.SIMULATOR.AGENT_0.SENSORS = agent_sensors

        # 设置最大episode_step
        config_env.ENVIRONMENT.MAX_EPISODE_STEPS = args.max_episode_length

        # 打乱
        config_env.ENVIRONMENT.ITERATOR_OPTIONS.SHUFFLE = False

        # RGB相机传感器参数
        config_env.SIMULATOR.RGB_SENSOR.WIDTH = args.env_frame_width
        config_env.SIMULATOR.RGB_SENSOR.HEIGHT = args.env_frame_height
        config_env.SIMULATOR.RGB_SENSOR.HFOV = args.hfov
        config_env.SIMULATOR.RGB_SENSOR.POSITION = [0, args.camera_height, 0]

        # Depth相机传感器参数
        config_env.SIMULATOR.DEPTH_SENSOR.WIDTH = args.env_frame_width
        config_env.SIMULATOR.DEPTH_SENSOR.HEIGHT = args.env_frame_height
        config_env.SIMULATOR.DEPTH_SENSOR.HFOV = args.hfov
        config_env.SIMULATOR.DEPTH_SENSOR.POSITION = [0, args.camera_height, 0]

        # 转弯角度
        config_env.SIMULATOR.TURN_ANGLE = 10

        # 数据划分
        config_env.DATASET.SPLIT = args.split

        config_env.freeze()
        env_configs.append(config_env)

        # 采用默认的baseline配置
        config_baseline = cfg_baseline()
        baseline_configs.append(config_baseline)

        args_list.append(args)

    envs = VectorEnv(
        make_env_fn=make_env_fn,
        env_fn_args=tuple(
            tuple(
                zip(args_list, env_configs, baseline_configs,
                    range(args.num_processes))
            )
        ),
    )

    return envs
