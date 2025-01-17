import numpy as np
import torch
from torch import nn
from torch.nn import functional as F


def get_grid(pose, grid_size, device):
    """
    Input:
        `pose` FloatTensor(bs, 3)
        `grid_size` 4-tuple (bs, _, grid_h, grid_w)
        `device` torch.device (cpu or gpu)
    Output:
        `rot_grid` FloatTensor(bs, grid_h, grid_w, 2)
        `trans_grid` FloatTensor(bs, grid_h, grid_w, 2)

    """
    pose = pose.float()
    x = pose[:, 0]
    y = pose[:, 1]
    t = pose[:, 2]

    bs = x.size(0)
    t = t * np.pi / 180.
    cos_t = t.cos()
    sin_t = t.sin()

    # n,3
    theta11 = torch.stack([cos_t, -sin_t,
                           torch.zeros(cos_t.shape).float().to(device)], 1)

    # n,3
    theta12 = torch.stack([sin_t, cos_t,
                           torch.zeros(cos_t.shape).float().to(device)], 1)
    # n,2,3
    # cos -sin 0
    # sin cos 0
    theta1 = torch.stack([theta11, theta12], 1)


    theta21 = torch.stack([torch.ones(x.shape).to(device),
                           -torch.zeros(x.shape).to(device), x], 1)
    theta22 = torch.stack([torch.zeros(x.shape).to(device),
                           torch.ones(x.shape).to(device), y], 1)
    # 1 0 x
    # - 1 y
    theta2 = torch.stack([theta21, theta22], 1)

    # n,grid_size,grid_size,2
    # 这里grid其实是，新的图像像素对应旧图像像素的坐标
    # 即，[2,4]向时针方向旋转是到达右边，但实际生成的grid是左边，
    # 因为相当于固定旋转后的图像不动，这样看来就是旧图像向顺时针方向旋转了
    rot_grid = F.affine_grid(theta1, torch.Size(grid_size))
    trans_grid = F.affine_grid(theta2, torch.Size(grid_size))

    return rot_grid, trans_grid


class ChannelPool(nn.MaxPool1d):
    def forward(self, x):
        n, c, w, h = x.size()
        x = x.view(n, c, w * h).permute(0, 2, 1)
        x = x.contiguous()
        pooled = F.max_pool1d(x, c, 1)
        _, _, c = pooled.size()
        pooled = pooled.permute(0, 2, 1)
        return pooled.view(n, c, w, h)


# https://github.com/ikostrikov/pytorch-a2c-ppo-acktr-gail/blob/master/a2c_ppo_acktr/utils.py#L32
class AddBias(nn.Module):
    def __init__(self, bias):
        super(AddBias, self).__init__()
        # 3,1
        self._bias = nn.Parameter(bias.unsqueeze(1))

    def forward(self, x):
        if x.dim() == 2:
            # 1,3
            bias = self._bias.t().view(1, -1)
        else:
            bias = self._bias.t().view(1, -1, 1, 1)

        # n,3
        return x + bias


# https://github.com/ikostrikov/pytorch-a2c-ppo-acktr-gail/blob/master/a2c_ppo_acktr/model.py#L10
class Flatten(nn.Module):
    def forward(self, x):
        return x.view(x.size(0), -1)


# https://github.com/ikostrikov/pytorch-a2c-ppo-acktr-gail/blob/master/a2c_ppo_acktr/model.py#L82
class NNBase(nn.Module):

    def __init__(self, recurrent, recurrent_input_size, hidden_size):
        '''
        Args:
            Global:
                recurrent: args.use_recurrent_global, # 0，全局不使用gru
                recurrent_input_size: g_hidden_size, # 256
                hidden_size: g_hidden_size # 256
            Local:
                recurrent: 1
                recurrent_input_size: 512
                hidden_size: 512
        '''
        super(NNBase, self).__init__()
        self._hidden_size = hidden_size
        self._recurrent = recurrent

        if recurrent:
            # 简化的GRU，是单个cell
            self.gru = nn.GRUCell(recurrent_input_size, hidden_size)
            nn.init.orthogonal_(self.gru.weight_ih.data)
            nn.init.orthogonal_(self.gru.weight_hh.data)
            self.gru.bias_ih.data.fill_(0)
            self.gru.bias_hh.data.fill_(0)

    @property
    def is_recurrent(self):
        return self._recurrent

    @property
    def rec_state_size(self):
        if self._recurrent:
            return self._hidden_size
        return 1

    @property
    def output_size(self):
        return self._hidden_size

    def _forward_gru(self, x, hxs, masks):
        if x.size(0) == hxs.size(0):
            # mask,对于一个完成的场景就mask掉
            x = hxs = self.gru(x, hxs * masks[:, None])
        else:
            # x is a (T, N, -1) tensor that has been flatten to (T * N, -1)
            # todo 这里应该是解决后面的场景比历史多的情况，但是没有理解
            N = hxs.size(0)
            # 这里怎么保证整除
            T = int(x.size(0) / N)

            # unflatten
            x = x.view(T, N, x.size(1))

            # Same deal with masks
            masks = masks.view(T, N, 1)

            outputs = []
            for i in range(T):
                hx = hxs = self.gru(x[i], hxs * masks[i])
                outputs.append(hx)

            # x is a (T, N, -1) tensor
            x = torch.stack(outputs, dim=0)
            # flatten
            x = x.view(T * N, -1)

        return x, hxs
