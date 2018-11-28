"""
    AirNet, implemented in PyTorch.
    Original paper: 'Attention Inspiring Receptive-Fields Network for Learning Invariant Representations,'
    https://ieeexplore.ieee.org/document/8510896.
"""

__all__ = ['AirNet', 'airnet50_1x64d_r2', 'airnet50_1x64d_r16', 'airnet101_1x64d_r2']

import os
import torch.nn as nn
import torch.nn.functional as F
import torch.nn.init as init


class ConvBlock(nn.Module):
    """
    Standard convolution block with Batch normalization and ReLU activation.

    Parameters:
    ----------
    in_channels : int
        Number of input channels.
    out_channels : int
        Number of output channels.
    kernel_size : int or tuple/list of 2 int
        Convolution window size.
    stride : int or tuple/list of 2 int
        Strides of the convolution.
    padding : int or tuple/list of 2 int
        Padding value for convolution layer.
    groups : int
        Number of groups.
    activate : bool
        Whether activate the convolution block.
    """
    def __init__(self,
                 in_channels,
                 out_channels,
                 kernel_size,
                 stride,
                 padding,
                 groups,
                 activate):
        super(ConvBlock, self).__init__()
        self.activate = activate

        self.conv = nn.Conv2d(
            in_channels=in_channels,
            out_channels=out_channels,
            kernel_size=kernel_size,
            stride=stride,
            padding=padding,
            groups=groups,
            bias=False)
        self.bn = nn.BatchNorm2d(num_features=out_channels)
        if self.activate:
            self.activ = nn.ReLU(inplace=True)

    def forward(self, x):
        x = self.conv(x)
        x = self.bn(x)
        if self.activate:
            x = self.activ(x)
        return x


def conv1x1_block(in_channels,
                  out_channels,
                  stride,
                  activate):
    """
    1x1 version of the standard convolution block.

    Parameters:
    ----------
    in_channels : int
        Number of input channels.
    out_channels : int
        Number of output channels.
    stride : int or tuple/list of 2 int
        Strides of the convolution.
    activate : bool
        Whether activate the convolution block.
    """
    return ConvBlock(
        in_channels=in_channels,
        out_channels=out_channels,
        kernel_size=1,
        stride=stride,
        padding=0,
        groups=1,
        activate=activate)


def conv3x3_block(in_channels,
                  out_channels,
                  stride,
                  groups=1,
                  activate=True):
    """
    Depthwise version of the standard convolution block.

    Parameters:
    ----------
    in_channels : int
        Number of input channels.
    out_channels : int
        Number of output channels.
    stride : int or tuple/list of 2 int
        Strides of the convolution.
    groups : int, default 1
        Number of groups.
    activate : bool, default True
        Whether activate the convolution block.
    """
    return ConvBlock(
        in_channels=in_channels,
        out_channels=out_channels,
        kernel_size=3,
        stride=stride,
        padding=1,
        groups=groups,
        activate=activate)


class AirBlock(nn.Module):
    """
    AirNet attention block.

    Parameters:
    ----------
    in_channels : int
        Number of input channels.
    out_channels : int
        Number of output channels.
    groups : int, default 1
        Number of groups.
    ratio: int, default 2
        Air compression ratio.
    """
    def __init__(self,
                 in_channels,
                 out_channels,
                 groups=1,
                 ratio=2):
        super(AirBlock, self).__init__()
        assert (out_channels % ratio == 0)
        mid_channels = out_channels // ratio

        self.conv1 = conv1x1_block(
            in_channels=in_channels,
            out_channels=mid_channels,
            stride=1,
            activate=True)
        self.pool = nn.MaxPool2d(
            kernel_size=3,
            stride=2,
            padding=1)
        self.conv2 = conv3x3_block(
            in_channels=mid_channels,
            out_channels=mid_channels,
            stride=1,
            groups=groups,
            activate=True)
        self.conv3 = conv1x1_block(
            in_channels=mid_channels,
            out_channels=out_channels,
            stride=1,
            activate=False)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        x = self.conv1(x)
        x = self.pool(x)
        x = self.conv2(x)
        x = F.interpolate(
            input=x,
            scale_factor=2,
            mode='bilinear',
            align_corners=True)
        x = self.conv3(x)
        x = self.sigmoid(x)
        return x


class AirBottleneck(nn.Module):
    """
    AirNet bottleneck block for residual path in AirNet unit.

    Parameters:
    ----------
    in_channels : int
        Number of input channels.
    out_channels : int
        Number of output channels.
    stride : int or tuple/list of 2 int
        Strides of the convolution.
    ratio: int
        Air compression ratio.
    """
    def __init__(self,
                 in_channels,
                 out_channels,
                 stride,
                 ratio):
        super(AirBottleneck, self).__init__()
        mid_channels = out_channels // 4
        self.use_air_block = (stride == 1 and mid_channels < 512)

        self.conv1 = conv1x1_block(
            in_channels=in_channels,
            out_channels=mid_channels,
            stride=1,
            activate=True)
        self.conv2 = conv3x3_block(
            in_channels=mid_channels,
            out_channels=mid_channels,
            stride=stride,
            activate=True)
        self.conv3 = conv1x1_block(
            in_channels=mid_channels,
            out_channels=out_channels,
            stride=1,
            activate=False)
        if self.use_air_block:
            self.air = AirBlock(
                in_channels=in_channels,
                out_channels=mid_channels,
                ratio=ratio)

    def forward(self, x):
        if self.use_air_block:
            att = self.air(x)
        x = self.conv1(x)
        x = self.conv2(x)
        if self.use_air_block:
            x = x * att
        x = self.conv3(x)
        return x


class AirUnit(nn.Module):
    """
    AirNet unit with residual connection.

    Parameters:
    ----------
    in_channels : int
        Number of input channels.
    out_channels : int
        Number of output channels.
    stride : int or tuple/list of 2 int
        Strides of the convolution.
    ratio: int
        Air compression ratio.
    """
    def __init__(self,
                 in_channels,
                 out_channels,
                 stride,
                 ratio):
        super(AirUnit, self).__init__()
        self.resize_identity = (in_channels != out_channels) or (stride != 1)

        self.body = AirBottleneck(
            in_channels=in_channels,
            out_channels=out_channels,
            stride=stride,
            ratio=ratio)
        if self.resize_identity:
            self.identity_conv = conv1x1_block(
                in_channels=in_channels,
                out_channels=out_channels,
                stride=stride,
                activate=False)
        self.activ = nn.ReLU(inplace=True)

    def forward(self, x):
        if self.resize_identity:
            identity = self.identity_conv(x)
        else:
            identity = x
        x = self.body(x)
        x = x + identity
        x = self.activ(x)
        return x


class AirInitBlock(nn.Module):
    """
    AirNet specific initial block.

    Parameters:
    ----------
    in_channels : int
        Number of input channels.
    out_channels : int
        Number of output channels.
    """
    def __init__(self,
                 in_channels,
                 out_channels):
        super(AirInitBlock, self).__init__()
        mid_channels = out_channels // 2

        self.conv1 = conv3x3_block(
            in_channels=in_channels,
            out_channels=mid_channels,
            stride=2,
            activate=True)
        self.conv2 = conv3x3_block(
            in_channels=mid_channels,
            out_channels=mid_channels,
            stride=1,
            activate=True)
        self.conv3 = conv3x3_block(
            in_channels=mid_channels,
            out_channels=out_channels,
            stride=1,
            activate=True)
        self.pool = nn.MaxPool2d(
            kernel_size=3,
            stride=2,
            padding=1)

    def forward(self, x):
        x = self.conv1(x)
        x = self.conv2(x)
        x = self.conv3(x)
        x = self.pool(x)
        return x


class AirNet(nn.Module):
    """
    AirNet model from 'Attention Inspiring Receptive-Fields Network for Learning Invariant Representations,'
    https://ieeexplore.ieee.org/document/8510896.

    Parameters:
    ----------
    channels : list of list of int
        Number of output channels for each unit.
    init_block_channels : int
        Number of output channels for the initial unit.
    ratio: int
        Air compression ratio.
    in_channels : int, default 3
        Number of input channels.
    in_size : tuple of two ints, default (224, 224)
        Spatial size of the expected input image.
    num_classes : int, default 1000
        Number of classification classes.
    """
    def __init__(self,
                 channels,
                 init_block_channels,
                 ratio,
                 in_channels=3,
                 in_size=(224, 224),
                 num_classes=1000):
        super(AirNet, self).__init__()
        self.in_size = in_size
        self.num_classes = num_classes

        self.features = nn.Sequential()
        self.features.add_module("init_block", AirInitBlock(
            in_channels=in_channels,
            out_channels=init_block_channels))
        in_channels = init_block_channels
        for i, channels_per_stage in enumerate(channels):
            stage = nn.Sequential()
            for j, out_channels in enumerate(channels_per_stage):
                stride = 2 if (j == 0) and (i != 0) else 1
                stage.add_module("unit{}".format(j + 1), AirUnit(
                    in_channels=in_channels,
                    out_channels=out_channels,
                    stride=stride,
                    ratio=ratio))
                in_channels = out_channels
            self.features.add_module("stage{}".format(i + 1), stage)
        self.features.add_module('final_pool', nn.AvgPool2d(
            kernel_size=7,
            stride=1))

        self.output = nn.Linear(
            in_features=in_channels,
            out_features=num_classes)

        self._init_params()

    def _init_params(self):
        for name, module in self.named_modules():
            if isinstance(module, nn.Conv2d):
                init.kaiming_uniform_(module.weight)
                if module.bias is not None:
                    init.constant_(module.bias, 0)

    def forward(self, x):
        x = self.features(x)
        x = x.view(x.size(0), -1)
        x = self.output(x)
        return x


def get_airnet(blocks,
               base_channels,
               ratio,
               model_name=None,
               pretrained=False,
               root=os.path.join('~', '.torch', 'models'),
               **kwargs):
    """
    Create AirNet model with specific parameters.

    Parameters:
    ----------
    blocks : int
        Number of blocks.
    base_channels: int
        Base number of channels.
    ratio: int
        Air compression ratio.
    model_name : str or None, default None
        Model name for loading pretrained model.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    root : str, default '~/.torch/models'
        Location for keeping the model parameters.
    """

    if blocks == 50:
        layers = [3, 4, 6, 3]
    elif blocks == 101:
        layers = [3, 4, 23, 3]
    else:
        raise ValueError("Unsupported AirNet with number of blocks: {}".format(blocks))

    bottleneck_expansion = 4
    init_block_channels = base_channels
    channels_per_layers = [base_channels * (2 ** i) * bottleneck_expansion for i in range(len(layers))]

    channels = [[ci] * li for (ci, li) in zip(channels_per_layers, layers)]

    net = AirNet(
        channels=channels,
        init_block_channels=init_block_channels,
        ratio=ratio,
        **kwargs)

    if pretrained:
        if (model_name is None) or (not model_name):
            raise ValueError("Parameter `model_name` should be properly initialized for loading pretrained model.")
        from .model_store import download_model
        download_model(
            net=net,
            model_name=model_name,
            local_model_store_dir_path=root)

    return net


def airnet50_1x64d_r2(**kwargs):
    """
    AirNet50-1x64d (r=2) model from 'Attention Inspiring Receptive-Fields Network for Learning Invariant
    Representations,' https://ieeexplore.ieee.org/document/8510896.

    Parameters:
    ----------
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    root : str, default '~/.torch/models'
        Location for keeping the model parameters.
    """
    return get_airnet(blocks=50, base_channels=64, ratio=2, model_name="airnet50_1x64d_r2", **kwargs)


def airnet50_1x64d_r16(**kwargs):
    """
    AirNet50-1x64d (r=16) model from 'Attention Inspiring Receptive-Fields Network for Learning Invariant
    Representations,' https://ieeexplore.ieee.org/document/8510896.

    Parameters:
    ----------
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    root : str, default '~/.torch/models'
        Location for keeping the model parameters.
    """
    return get_airnet(blocks=50, base_channels=64, ratio=16, model_name="airnet50_1x64d_r16", **kwargs)


def airnet101_1x64d_r2(**kwargs):
    """
    AirNet101-1x64d (r=2) model from 'Attention Inspiring Receptive-Fields Network for Learning Invariant
    Representations,' https://ieeexplore.ieee.org/document/8510896.

    Parameters:
    ----------
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    root : str, default '~/.torch/models'
        Location for keeping the model parameters.
    """
    return get_airnet(blocks=101, base_channels=64, ratio=2, model_name="airnet101_1x64d_r2", **kwargs)


def _calc_width(net):
    import numpy as np
    net_params = filter(lambda p: p.requires_grad, net.parameters())
    weight_count = 0
    for param in net_params:
        weight_count += np.prod(param.size())
    return weight_count


def _test():
    import torch
    from torch.autograd import Variable

    pretrained = False

    models = [
        airnet50_1x64d_r2,
        airnet50_1x64d_r16,
        airnet101_1x64d_r2,
    ]

    for model in models:

        net = model(pretrained=pretrained)

        # net.train()
        net.eval()
        weight_count = _calc_width(net)
        print("m={}, {}".format(model.__name__, weight_count))
        assert (model != airnet50_1x64d_r2 or weight_count == 27425864)
        assert (model != airnet50_1x64d_r16 or weight_count == 25714952)
        assert (model != airnet101_1x64d_r2 or weight_count == 51727432)

        x = Variable(torch.randn(1, 3, 224, 224))
        y = net(x)
        assert (tuple(y.size()) == (1, 1000))


if __name__ == "__main__":
    _test()
