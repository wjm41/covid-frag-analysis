# Author: Arian Jamasb
"""
Property prediction using a Message-Passing Neural Network.
"""

import argparse
import os

import dgl
import numpy as np
import pandas as pd
import torch
from tqdm import tqdm
from sklearn.metrics import roc_auc_score, auc, precision_recall_curve
from sklearn.model_selection import train_test_split
from torch.nn import functional as F
from torch.nn import BCELoss
from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter

from mpnn import MPNNPairPredictor
from parser import return_pairs

#Set torch variables
torch.autograd.set_detect_anomaly(True)

if torch.cuda.is_available():
    print('use GPU')
    device = 'cuda'
else:
    print('use CPU')
    device = 'cpu'

# Collate Function for Dataloader
def collate(sample):
    graphs_high, graphs_low, labels = map(list, zip(*sample))
    batched_graph_high = dgl.batch(graphs_high)
    batched_graph_high.set_n_initializer(dgl.init.zero_initializer)
    batched_graph_high.set_e_initializer(dgl.init.zero_initializer)
    batched_graph_low = dgl.batch(graphs_low)
    batched_graph_low.set_n_initializer(dgl.init.zero_initializer)
    batched_graph_low.set_e_initializer(dgl.init.zero_initializer)
    labels = torch.tensor(labels).reshape(len(labels),1)
    return batched_graph_high, batched_graph_low , labels

def main(args):
    """
    :param n_trials: int specifying number of random train/test splits to use
    :param test_set_size: float in range [0, 1] specifying fraction of dataset to use as test set
    """

    df3 = pd.read_csv('data/rest_activity.smi')

    roc_list = []
    prc_list = []

    for i in range(args.n_trials):
        writer = SummaryWriter('runs/'+args.savename+'/run_'+str(i))

        if args.test:
            df3_train, df3_test = train_test_split(df3, stratify=df3['activity'],
                                                 test_size=args.test_set_size, shuffle=True, random_state=i+5)

            X3_high_train, X3_low_train, y3_train, _, _ = return_pairs(df3_train)
            X3_high_test, X3_low_test, y3_test, n_feats, e_feats = return_pairs(df3_test, test=False)

            X_high_train =  X3_high_train
            X_low_train = X3_low_train
            # X_high_test = np.concatenate([X1_high_test,X2_high_test, X3_high_test])
            # X_low_test = np.concatenate([X1_low_test,X2_low_test, X3_low_test])

            y_train =y3_train
            # y_test = np.concatenate([y1_test, y2_test, y3_test])

            y_train = torch.Tensor(y_train)
            y1_test = torch.Tensor(y1_test)
            y2_test = torch.Tensor(y2_test)
            y3_test = torch.Tensor(y3_test)

            train_data = list(zip(X_high_train,X_low_train, y_train ))
            test_rest = list(zip(X3_high_test, X3_low_test, y3_test))

            train_loader = DataLoader(train_data, batch_size=32, shuffle=True, collate_fn=collate, drop_last=False)
            test_rest = DataLoader(test_rest, batch_size=32, shuffle=True, collate_fn=collate, drop_last=False)
        else:
            X3_high, X3_low, y3, n_feats, e_feats = return_pairs(df3)

            X_high =  X3_high
            X_low = X3_low

            y =  y3

            y = torch.Tensor(y)

            train_data = list(zip(X_high, X_low, y))

            train_loader = DataLoader(train_data, batch_size=32, shuffle=True, collate_fn=collate, drop_last=False)

        n_tasks = 1
        mpnn_net = MPNNPairPredictor(node_in_feats=n_feats,
                                       edge_in_feats=e_feats,
                                       node_out_feats=128,
                                       n_tasks=n_tasks)
        mpnn_net = mpnn_net.to(device)

        class_loss_fn = BCELoss()

        optimizer = torch.optim.Adam(mpnn_net.parameters(), lr=args.lr)

        for epoch in tqdm(range(1, args.n_epochs+1)):
            epoch_loss = 0
            preds = []
            labs = []
            mpnn_net.train()
            n=0
            for i, (bg_high, bg_low, labels) in tqdm(enumerate(train_loader)):
                labels = labels.to(device)
                atom_feats_high = bg_high.ndata.pop('h').to(device)
                bond_feats_high = bg_high.edata.pop('e').to(device)
                atom_feats_low = bg_low.ndata.pop('h').to(device)
                bond_feats_low = bg_low.edata.pop('e').to(device)
                y_pred = mpnn_net(bg_high, atom_feats_high, bond_feats_high, bg_low, atom_feats_low, bond_feats_low)
                # y_pred = F.softmax(y_pred, dim=0)
                #y_pred = torch.exp(y_pred)
                y_pred = F.sigmoid(y_pred)

                loss = torch.tensor(0)
                loss = loss.to(device)

                if args.debug:
                    print('label: {}'.format(labels))
                    print('y_pred: {}'.format(y_pred))

                loss = loss + class_loss_fn(y_pred,
                                            labels)
                if args.debug:
                    print('loss: {}'.format(loss))
                optimizer.zero_grad()
                loss.backward()

                optimizer.step()
                epoch_loss += loss.detach().item()

                labels = labels.cpu().numpy()
                y_pred = y_pred.detach().cpu().numpy()

                # store labels and preds
                preds.append(y_pred)
                labs.append(labels)

            labs = np.concatenate(labs, axis=0)
            preds = np.concatenate(preds, axis=0)

            roc = roc_auc_score(labs,
                               preds)
            precision, recall, thresholds = precision_recall_curve(labs,
                                                                   preds)
            prc = auc(recall, precision)
            if args.debug:
                print('roc: {}'.format(roc))
                print('prc: {}'.format(prc))
            writer.add_scalar('LOSS/train', epoch_loss, epoch)
            writer.add_scalar('train/pair_rocauc', roc, epoch)
            writer.add_scalar('train/pair_prcauc', prc, epoch)


            # if epoch % 20 == 0:
            #     print(f"\nepoch: {epoch}, "
            #           f"LOSS: {epoch_loss:.3f}"
            #           f"\n pair ROC-AUC: {roc:.3f}, "
            #           f"pair PRC-AUC: {prc:.3f}")
            #
            #     try:
            #         torch.save(mpnn_net.state_dict(), '/rds-d2/user/wjm41/hpc-work/models/' + args.savename +
            #                    '/model_epoch_' + str(epoch) + '.pt')
            #     except FileNotFoundError:
            #         cmd = 'mkdir /rds-d2/user/wjm41/hpc-work/models/' + args.savename
            #         os.system(cmd)
            #         torch.save(mpnn_net.state_dict(), '/rds-d2/user/wjm41/hpc-work/models/' + args.savename +
            #                    '/model_epoch_' + str(epoch) + '.pt')
            if args.test:
                mpnn_net.eval()
                preds = []
                labs = []
                for i, (bg_high, bg_low, labels) in enumerate(test_rest):
                    labels = labels.to(device)
                    atom_feats_high = bg_high.ndata.pop('h').to(device)
                    bond_feats_high = bg_high.edata.pop('e').to(device)
                    atom_feats_low = bg_low.ndata.pop('h').to(device)
                    bond_feats_low = bg_low.edata.pop('e').to(device)
                    y_pred = mpnn_net(bg_high, atom_feats_high, bond_feats_high, bg_low, atom_feats_low, bond_feats_low)
                    y_pred = F.sigmoid(y_pred)

                    labels = labels.cpu().numpy()
                    y_pred = y_pred.detach().cpu().numpy()

                    preds.append(y_pred)
                    labs.append(labels)

                labs = np.concatenate(labs, axis=0)
                preds = np.concatenate(preds, axis=0)

                roc = roc_auc_score(labs,
                                   preds)
                precision, recall, thresholds = precision_recall_curve(labs,
                                                                       preds)
                prc = auc(recall, precision)

                writer.add_scalar('test/pair_rest_rocauc', roc, epoch)
                writer.add_scalar('test/pair_rest_prcauc', prc, epoch)


                if epoch==(args.n_epochs):
                    print(f"\n======================== TEST ========================"
                          f"\n pair ROC-AUC: {roc:.3f}, "
                          f"pair PRC-AUC: {prc:.3f}")

                    roc_list.append(roc)
                    prc_list.append(prc)

        torch.save(mpnn_net.state_dict(), '/rds-d2/user/wjm41/hpc-work/models/' + args.savename +
                   '/model_epoch_final.pt')
    if args.test:
        roc_list = np.array(roc_list).T
        prc_list = np.array(prc_list).T

        print("\n TEST")
        print("ROC-AUC: {:.3f} +- {:.3f}".format(np.mean(roc_list[0]), np.std(roc_list[0]) / np.sqrt(len(roc_list[0]))))
        print("PRC-AUC: {:.3f} +- {:.3f}".format(np.mean(prc_list[0]), np.std(prc_list[0]) / np.sqrt(len(prc_list[0]))))


if __name__ == '__main__':

    parser = argparse.ArgumentParser()

    parser.add_argument('-n_trials', '--n_trials', type=int, default=3,
                        help='int specifying number of random train/test splits to use')
    parser.add_argument('-n_epochs', type=int, default=200,
                        help='int specifying number of epochs for training')
    parser.add_argument('-savename', '--savename', type=str, default='multitask_pair',
                        help='name for directory containing saved model params and tensorboard logs')
    parser.add_argument('-ts', '--test_set_size', type=float, default=0.2,
                        help='float in range [0, 1] specifying fraction of dataset to use as test set')
    parser.add_argument('-lr', '--lr', type=float, default=1e-3,
                        help='float specifying learning rate used during training.')
    parser.add_argument('-dry', action='store_true',
                        help='whether or not to only use a subset of the HTS screen')
    parser.add_argument('-multi', action='store_true',
                        help='whether or not to do multitask')
    parser.add_argument('-test', action='store_true',
                        help='whether or not to do test/train split')
    parser.add_argument('-test_HTS', action='store_true',
                        help='whether or not to include HTS data')
    parser.add_argument('-debug', action='store_true',
                        help='whether or not to print predictions and model weight gradients')
    args = parser.parse_args()

    main(args)
