import argparse
import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
import sys
from pathlib import Path
from tqdm import tqdm
import matplotlib.pyplot as plt
from torch.utils.tensorboard import SummaryWriter
writer = SummaryWriter()

from script.Dataset import Dataset
from script.Model import ResNet, ResNet18, VGG11, OrientationLoss

import torch
import torch.nn as nn
from torchvision.models import resnet18, vgg11
from torch.utils import data

FILE = Path(__file__).resolve()
ROOT = FILE.parents[0]  # YOLOv5 root directory
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))  # add ROOT to PATH
ROOT = Path(os.path.relpath(ROOT, Path.cwd()))  # relative

# model factory to choose model
model_factory = {
    'resnet': resnet18(weights=False),
    'resnet18': resnet18(weights=False),
    'vgg11': vgg11(weights=False)
}
regressor_factory = {
    'resnet': ResNet,
    'resnet18': ResNet18,
    'vgg11': VGG11
}


def save_loss(data, save_path):
    # 创建一个x轴的值，对应列表中的每个数据点
    x = list(range(len(data)))

    # 创建图像
    plt.figure(figsize=(10, 5))  # 设置图像大小

    # 绘制线图
    plt.plot(x, data, marker='o')

    # 添加标题和标签
    plt.title('Data Plot')  # 图像标题
    plt.xlabel('Index')  # x轴标签
    plt.ylabel('Value')  # y轴标签
    plt.grid(True)
    plt.savefig(save_path)


def train(epochs=10, batch_size=32, alpha=0.6, w=0.4, num_workers=2, lr=0.0001, save_epoch=10,
          train_path=ROOT / 'dataset/KITTI/training', model_path=ROOT / 'weights/', select_model='resnet18'):
    # directory
    train_path = str(train_path)
    model_path = str(model_path)
    loss_path = writer.log_dir + "/loss.png"

    # dataset
    print('[INFO] Loading dataset...')
    dataset = Dataset(train_path)

    # hyper_params
    hyper_params = {
        'epochs': epochs,
        'batch_size': batch_size,
        'w': w,
        'num_workers': num_workers,
        'lr': lr,
        'shuffle': True
    }

    # data generator
    data_gen = data.DataLoader(
        dataset,
        batch_size=hyper_params['batch_size'],
        shuffle=hyper_params['shuffle'],
        num_workers=hyper_params['num_workers'])

    # model
    base_model = model_factory[select_model]
    model = regressor_factory[select_model](model=base_model).cuda()

    # optimizer
    opt_SGD = torch.optim.SGD(model.parameters(), lr=hyper_params['lr'], momentum=0.9)

    # loss function
    conf_loss_func = nn.CrossEntropyLoss().cuda()
    dim_loss_func = nn.MSELoss().cuda()
    orient_loss_func = OrientationLoss

    # load previous weights
    latest_model = None
    first_epoch = 1
    if not os.path.isdir(model_path):
        os.mkdir(model_path)
    else:
        latest_model = select_model + '.pkl'

    if latest_model is not None:
        checkpoint = torch.load(model_path + "/" + latest_model)
        model.load_state_dict(checkpoint['model_state_dict'])
        opt_SGD.load_state_dict(checkpoint['optimizer_state_dict'])
        first_epoch = checkpoint['epoch']
        loss = checkpoint['loss']

        print(f'[INFO] Using previous model {latest_model} at {first_epoch} epochs')
        print('[INFO] Resuming training...')

    all_loss = []
    for epoch in range(first_epoch, int(hyper_params['epochs']) + 1):
        losses = []
        with tqdm(data_gen, unit='batch') as tepoch:
            for local_batch, local_labels in tepoch:
                # progress bar
                tepoch.set_description(f'Epoch {epoch}')

                # ground-truth
                truth_orient = local_labels['Orientation'].float().cuda()
                truth_conf = local_labels['Confidence'].float().cuda()
                truth_dim = local_labels['Dimensions'].float().cuda()

                # convert to cuda
                local_batch = local_batch.float().cuda()

                # forward
                [orient, conf, dim] = model(local_batch)

                # loss
                orient_loss = orient_loss_func(orient, truth_orient, truth_conf)
                dim_loss = dim_loss_func(dim, truth_dim)

                truth_conf = torch.max(truth_conf, dim=1)[1]
                conf_loss = conf_loss_func(conf, truth_conf)

                loss_theta = conf_loss + w * orient_loss
                loss = alpha * dim_loss + loss_theta
                losses.append(loss.item())

                # write tensorboard and comet ml
                writer.add_scalar('Loss/train', loss, epoch)

                opt_SGD.zero_grad()
                loss.backward()
                opt_SGD.step()

                # progress bar update
                tepoch.set_postfix(loss=loss.item())
        all_loss.append(sum(losses) / len(losses))
        if epoch % save_epoch == 0:
            model_name = os.path.join(model_path, f'{select_model}_epoch_{epoch}.pkl')
            torch.save({
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': opt_SGD.state_dict(),
                'loss': loss
            }, model_name)
            print(f'[INFO] Saving weights as {model_name}')

    save_loss(all_loss, loss_path)
    print(f'[INFO] Saving loss png  as {loss_path}')
    writer.flush()
    writer.close()


def parse_opt():
    parser = argparse.ArgumentParser(description='Regressor Model Training')
    parser.add_argument('--epochs', type=int, default=20, help='Number of epochs')
    parser.add_argument('--batch_size', type=int, default=32, help='Number of batch size')
    parser.add_argument('--alpha', type=float, default=0.6, help='Aplha default=0.6 DONT CHANGE')
    parser.add_argument('--w', type=float, default=0.4, help='w DONT CHANGE')
    parser.add_argument('--num_workers', type=int, default=2, help='Total # workers, for colab & kaggle use 2')
    parser.add_argument('--lr', type=float, default=0.0001, help='Learning rate')
    parser.add_argument('--save_epoch', type=int, default=10, help='Save model every # epochs')
    parser.add_argument('--train_path', type=str, default=ROOT / 'dataset/KITTI/training', help='Training path KITTI')
    parser.add_argument('--model_path', type=str, default=ROOT / 'weights',
                        help='Weights path, for load and save model')
    parser.add_argument('--select_model', type=str, default='resnet18',
                        help='Model selection: {resnet, resnet18, vgg11}')
    opt = parser.parse_args()
    return opt


def main(opt):
    train(**vars(opt))


if __name__ == '__main__':
    opt = parse_opt()
    main(opt)
