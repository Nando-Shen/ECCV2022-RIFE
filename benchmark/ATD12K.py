import os
import sys
sys.path.append('.')
import cv2
import math
import torch
import argparse
import numpy as np
from torch.nn import functional as F
from model.pytorch_msssim import ssim_matlab
from model.RIFE import Model
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

model = Model()
model.load_model('train_log')
model.eval()
model.device()

path = '/content/drive/MyDrive/atd12k_points/test_2k_540p/'
dirs = os.listdir(path)
psnr_list = []
ssim_list = []
print(len(dirs))
for d in dirs:
    img0 = (path + d + '/frame1.jpg')
    img1 = (path + d + '/frame3.jpg')
    gt = (path + d + '/frame2.jpg')
    img0 = (torch.tensor(cv2.imread(img0).transpose(2, 0, 1) / 255.)).to(device).float().unsqueeze(0)
    img1 = (torch.tensor(cv2.imread(img1).transpose(2, 0, 1) / 255.)).to(device).float().unsqueeze(0)
    gt = (torch.tensor(cv2.imread(gt).transpose(2, 0, 1) / 255.)).to(device).float().unsqueeze(0)
    pader = torch.nn.ReplicationPad2d([0, 0, 2, 2])
    img0 = pader(img0)
    img1 = pader(img1)
    pred = model.inference(img0, img1)[0][:, 2:-2]
    ssim = ssim_matlab(gt, torch.round(pred * 255).unsqueeze(0) / 255.).detach().cpu().numpy()
    out = pred.detach().cpu().numpy().transpose(1, 2, 0)
    out = np.round(out * 255) / 255.
    gt = gt[0].cpu().numpy().transpose(1, 2, 0)
    psnr = -10 * math.log10(((gt - out) * (gt - out)).mean())
    psnr_list.append(psnr)
    ssim_list.append(ssim)
    print("Avg PSNR: {} SSIM: {}".format(np.mean(psnr_list), np.mean(ssim_list)))
