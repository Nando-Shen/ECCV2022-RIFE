import os
import math
import time
import torch
import numpy as np
import random
import argparse

from model.RIFE import Model
from torch.utils.data import DataLoader, Dataset
from tensorboardX import SummaryWriter
from atd12k import get_loader
from utils.pytorch_msssim import ssim_matlab
import torchvision.transforms as T

device = torch.device("cuda")
MSE_LossFn = torch.nn.MSELoss()

transform = T.ToPILImage()
log_path = 'train_log'

def get_learning_rate(step):
    if step < 2000:
        mul = step / 2000.
        return 5e-4 * mul
    else:
        mul = np.cos((step - 2000) / (args.epoch * args.step_per_epoch - 2000.) * math.pi) * 0.5 + 0.5
        return (3e-4 - 3e-6) * mul + 3e-6

def flow2rgb(flow_map_np):
    h, w, _ = flow_map_np.shape
    rgb_map = np.ones((h, w, 3)).astype(np.float32)
    normalized_flow_map = flow_map_np / (np.abs(flow_map_np).max())
    
    rgb_map[:, :, 0] += normalized_flow_map[:, :, 0]
    rgb_map[:, :, 1] -= 0.5 * (normalized_flow_map[:, :, 0] + normalized_flow_map[:, :, 1])
    rgb_map[:, :, 2] += normalized_flow_map[:, :, 1]
    return rgb_map.clip(0, 1)

def train(model, local_rank):
    if local_rank == 0:
        writer = SummaryWriter('train')
        writer_val = SummaryWriter('validate')
    step = 0
    nr_eval = 0
    # dataset = VimeoDataset('train')
    # sampler = DistributedSampler(dataset)

    train_data, train_length = get_loader('train', args.train, args.batch_size, shuffle=True)
    val_data, val_length = get_loader('test', args.train, args.batch_size, shuffle=False)
    # train_data = DataLoader(dataset, batch_size=args.batch_size, num_workers=8, pin_memory=True, drop_last=True, sampler=sampler)
    args.step_per_epoch = train_length / args.batch_size
    # dataset_val = VimeoDataset('validation')
    # val_data = DataLoader(dataset_val, batch_size=16, pin_memory=True, num_workers=8)
    print('training...')
    time_stamp = time.time()
    for epoch in range(args.epoch):
        # sampler.set_epoch(epoch)
        print('Epoch: {}'.format(epoch))
        # evaluate(model, val_data, step, local_rank, writer_val)
        for i, data in enumerate(train_data):
            data_time_interval = time.time() - time_stamp
            time_stamp = time.time()
            data_gpu = data
            data_gpu = data_gpu.to(device, non_blocking=True)
            # print(data_gpu)
            imgs = data_gpu[:, :6]
            gt = data_gpu[:, 6:9]
            learning_rate = get_learning_rate(step)
            pred, info = model.update(imgs, gt, learning_rate, training=True) # pass timestep if you are training RIFEm
            train_time_interval = time.time() - time_stamp
            time_stamp = time.time()
            if step % 200 == 1 and local_rank == 0:
                writer.add_scalar('learning_rate', learning_rate, step)
                writer.add_scalar('loss/l1', info['loss_l1'], step)
                writer.add_scalar('loss/tea', info['loss_tea'], step)
                writer.add_scalar('loss/distill', info['loss_distill'], step)
            # if step % 1000 == 1 and local_rank == 0:
            #     gt = (gt.permute(0, 2, 3, 1).detach().cpu().numpy() * 255).astype('uint8')
            #     mask = (torch.cat((info['mask'], info['mask_tea']), 3).permute(0, 2, 3, 1).detach().cpu().numpy() * 255).astype('uint8')
            #     pred = (pred.permute(0, 2, 3, 1).detach().cpu().numpy() * 255).astype('uint8')
            #     merged_img = (info['merged_tea'].permute(0, 2, 3, 1).detach().cpu().numpy() * 255).astype('uint8')
            #     flow0 = info['flow'].permute(0, 2, 3, 1).detach().cpu().numpy()
            #     flow1 = info['flow_tea'].permute(0, 2, 3, 1).detach().cpu().numpy()
            #     for i in range(5):
            #         imgs = np.concatenate((merged_img[i], pred[i], gt[i]), 1)[:, :, ::-1]
            #         writer.add_image(str(i) + '/img', imgs, step, dataformats='HWC')
            #         writer.add_image(str(i) + '/flow', np.concatenate((flow2rgb(flow0[i]), flow2rgb(flow1[i])), 1), step, dataformats='HWC')
            #         writer.add_image(str(i) + '/mask', mask[i], step, dataformats='HWC')
            #     writer.flush()
            if local_rank == 0 and i%100==0:
                print('epoch:{} {}/{} time:{:.2f}+{:.2f} loss_l1:{:.4e}'.format(epoch, i, args.step_per_epoch, data_time_interval, train_time_interval, info['loss_l1']))
            step += 1
        nr_eval += 1
        if nr_eval % 5 == 0:
            evaluate(model, val_data, step, local_rank, writer_val)
        model.save_model(log_path, local_rank)    

def evaluate(model, val_data, nr_eval, local_rank, writer_val):
    loss_l1_list = []
    loss_distill_list = []
    loss_tea_list = []
    psnr_list = []
    ssim = 0
    psnr_list_teacher = []
    psnrr = 0
    # time_stamp = time.time()
    print('Start evaluate')
    for i, data in enumerate(val_data):
        data_gpu, dir = data
        data_gpu = data_gpu.to(device, non_blocking=True)
        imgs = data_gpu[:, :6]
        gt = data_gpu[:, 6:9]
        with torch.no_grad():
            pred, info = model.update(imgs, gt, training=False)
            merged_img = info['merged_tea']
        loss_l1_list.append(info['loss_l1'].cpu().numpy())
        loss_tea_list.append(info['loss_tea'].cpu().numpy())
        loss_distill_list.append(info['loss_distill'].cpu().numpy())
        for j in range(gt.shape[0]):
            psnr = -10 * math.log10(torch.mean((gt[j] - pred[j]) * (gt[j] - pred[j])).cpu().data)
            psnr_list.append(psnr)
            psnr = -10 * math.log10(torch.mean((merged_img[j] - gt[j]) * (merged_img[j] - gt[j])).cpu().data)
            psnr_list_teacher.append(psnr)
            pp = transform(pred[j])
            os.makedirs('/home/jiaming/rife'+ '/{}'.format(dir[j]), exist_ok=True)
            pp.save('/home/jiaming/rife' + '/{}/rife.png'.format(dir[j]))
        MSE_val = MSE_LossFn(pred, gt)
        psnrr += (10 * math.log10(1 / MSE_val.item()))
        ssim += ssim_matlab(gt.clamp(0, 1), pred.clamp(0, 1), val_range=1.)
        gt = (gt.permute(0, 2, 3, 1).cpu().numpy() * 255).astype('uint8')
        pred = (pred.permute(0, 2, 3, 1).cpu().numpy() * 255).astype('uint8')
        merged_img = (merged_img.permute(0, 2, 3, 1).cpu().numpy() * 255).astype('uint8')
        flow0 = info['flow'].permute(0, 2, 3, 1).cpu().numpy()
        # flow1 = info['flow_tea'].permute(0, 2, 3, 1).cpu().numpy()
        if i == 0 and local_rank == 0:
            for j in range(10):
                imgs = np.concatenate((merged_img[j], pred[j], gt[j]), 1)[:, :, ::-1]
                writer_val.add_image(str(j) + '/img', imgs.copy(), nr_eval, dataformats='HWC')
                writer_val.add_image(str(j) + '/flow', flow2rgb(flow0[j][:, :, ::-1]), nr_eval, dataformats='HWC')

    # eval_time_interval = time.time() - time_stamp

    if local_rank != 0:
        return
    ppsnr = np.array(psnr_list).mean()
    ppsnr_teacher = np.array(psnr_list_teacher).mean()
    sssim = ssim / len(val_data)
    ppsnrr = psnrr / len(val_data)
    writer_val.add_scalar('psnr', ppsnr, nr_eval)
    writer_val.add_scalar('psnr_teacher', ppsnr_teacher, nr_eval)
    writer_val.add_scalar('ssim', sssim, nr_eval)
    writer_val.add_scalar('psnrr', ppsnrr, nr_eval)
    print("Epoch: ", nr_eval)
    print("ValPSNR: %0.4f ValPSNR_TEA: %0.4f ValSSIM: %0.4f, ValPSNRR: %0.4f" % (ppsnr, ppsnr_teacher, sssim, ppsnrr))
        
if __name__ == "__main__":    
    parser = argparse.ArgumentParser()
    parser.add_argument('--epoch', default=20, type=int)
    parser.add_argument('--batch_size', default=16, type=int, help='minibatch size')
    parser.add_argument('--local_rank', default=0, type=int, help='local rank')
    parser.add_argument('--train', type=str, default='/home/jiaming/atd12k_points', help='dataset')

    args = parser.parse_args()
    torch.cuda.set_device(args.local_rank)
    seed = 1234
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = True
    model = Model(args.local_rank)
    train(model, args.local_rank)
        
