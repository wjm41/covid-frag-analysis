# Author: Arian Jamasb
"""
Property prediction using a Message-Passing Neural Network.
"""

import argparse

import dgl
from tqdm import tqdm
import numpy as np
import torch
from dgllife.model.model_zoo import MPNNPredictor
from dgllife.utils import CanonicalAtomFeaturizer, CanonicalBondFeaturizer, mol_to_bigraph
from rdkit import Chem
from scipy.stats import pearsonr
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error, roc_auc_score, auc, precision_recall_curve
from sklearn.model_selection import train_test_split, StratifiedKFold
from sklearn.preprocessing import StandardScaler
import torch.nn.functional as F
from torch.nn import MSELoss, NLLLoss, BCELoss
from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter

from helper import parse_dataset
# from data_utils import TaskDataLoader

# Adjust accordingly for your own file system
FREESOLV_PATH = 'data/FreeSolv/FreeSolv.csv'
CATS_PATH = 'data/CatS/CatS.csv'
LIPO_PATH = 'data/lipo/lipo.csv'
ESOL_PATH = 'data/esol/esol.csv'
dls_PATH = 'data/dls/dls.csv'
sars_PATH = 'data/sars_s4.csv'
SARS_PATH = 'data/sars_s4.csv'
ACRY_PATH = 'data/acry_activity.smi'
CHLORO_PATH = 'data/chloroace_activity.smi'
REST_PATH = 'data/rest_activity.smi'
BRADLEY_PATH = 'data/bradley/bradley.csv'
MALARIA_PATH = 'data/Malaria/Malaria.csv'
CHEMBL5118_PATH = 'data/CHEMBL5118.csv'
CHEMBL3927_PATH = 'data/CHEMBL3927.csv'

PATHS = {'FreeSolv': FREESOLV_PATH, 'esol': ESOL_PATH, 'lipo': LIPO_PATH, 'dls': dls_PATH, 'CatS':CATS_PATH,
         'bradley':BRADLEY_PATH, 'Malaria':MALARIA_PATH, 'CHEMBL5118': CHEMBL5118_PATH, 'CHEMBL3927' : CHEMBL3927_PATH,
         'CHEMBL5118_typical': CHEMBL5118_PATH, 'CHEMBL3927_typical' : CHEMBL3927_PATH, 'sars':sars_PATH,
         'SARS':SARS_PATH, 'acry':ACRY_PATH, 'chloro': CHLORO_PATH,'rest': REST_PATH}
if torch.cuda.is_available():
    print('use GPU')
    device = 'cuda'
else:
    print('use CPU')
    device = 'cpu'

# Collate Function for Dataloader
def collate(sample):
    graphs, labels = map(list, zip(*sample))
    batched_graph = dgl.batch(graphs)
    batched_graph.set_n_initializer(dgl.init.zero_initializer)
    batched_graph.set_e_initializer(dgl.init.zero_initializer)
    return batched_graph, torch.tensor(labels)

def main(args):
    """
    :param path: str specifying path to dataset.
    :param task: str specifying the task. One of ['e_iso_pi', 'z_iso_pi', 'e_iso_n', 'z_iso_n']
    :param n_trials: int specifying number of random train/test splits to use
    :param test_set_size: float in range [0, 1] specifying fraction of dataset to use as test set
    """

    # data_loader = TaskDataLoader(args.task, args.path)
    # smiles_list, y = data_loader.load_property_data()

    smiles_list, y = parse_dataset(args.task, PATHS[args.task], args.reg)
    X = [Chem.MolFromSmiles(m) for m in smiles_list]

    # Initialise featurisers
    atom_featurizer = CanonicalAtomFeaturizer()
    bond_featurizer = CanonicalBondFeaturizer()

    e_feats = bond_featurizer.feat_size('e')
    n_feats = atom_featurizer.feat_size('h')
    print('Number of features: ', n_feats)

    X = [mol_to_bigraph(m, node_featurizer=atom_featurizer, edge_featurizer=bond_featurizer) for m in X]

    r2_list = []
    rmse_list = []
    mae_list = []
    skipped_trials = 0

    for i in range(args.n_trials):

        # X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=args.test_set_size, random_state=i + 5)

        kf = StratifiedKFold(n_splits=args.n_folds, random_state=i, shuffle=True)
        split_list = kf.split(X, y)
        j=0
        for train_ind, test_ind in split_list:
            if args.reg:
                writer = SummaryWriter('runs/'+args.task+'/mpnn/reg/run_'+str(i)+'_fold_'+str(j))
            else:
                writer = SummaryWriter('runs/'+args.task+'/mpnn/class/run_'+str(i)+'_fold_'+str(j))
            X_train, X_test = np.array(X)[train_ind], np.array(X)[test_ind]
            y_train, y_test = np.array(y)[train_ind], np.array(y)[test_ind]

            y_train = y_train.reshape(-1, 1)
            y_test = y_test.reshape(-1, 1)

            #  We standardise the outputs but leave the inputs unchanged
            if args.reg:
                y_scaler = StandardScaler()
                y_train_scaled = torch.Tensor(y_scaler.fit_transform(y_train))
                y_test_scaled = torch.Tensor(y_scaler.transform(y_test))
            else:
                y_train_scaled = torch.Tensor(y_train)
                y_test_scaled = torch.Tensor(y_test)

            train_data = list(zip(X_train, y_train_scaled))
            test_data = list(zip(X_test, y_test_scaled))

            train_loader = DataLoader(train_data, batch_size=32, shuffle=True, collate_fn=collate, drop_last=False)
            test_loader = DataLoader(test_data, batch_size=32, shuffle=False, collate_fn=collate, drop_last=False)

            mpnn_net = MPNNPredictor(node_in_feats=n_feats,
                                     edge_in_feats=e_feats
                                     )
            mpnn_net.to(device)

            if args.reg:
                loss_fn = MSELoss()
            else:
                loss_fn = BCELoss()
            optimizer = torch.optim.Adam(mpnn_net.parameters(), lr=1e-4)

            mpnn_net.train()

            epoch_losses = []
            epoch_rmses = []
            for epoch in tqdm(range(1, args.n_epochs)):
                epoch_loss = 0
                preds = []
                labs = []
                for i, (bg, labels) in tqdm(enumerate(train_loader)):
                    labels = labels.to(device)
                    atom_feats = bg.ndata.pop('h').to(device)
                    bond_feats = bg.edata.pop('e').to(device)
                    atom_feats, bond_feats, labels = atom_feats.to(device), bond_feats.to(device), labels.to(device)
                    y_pred = mpnn_net(bg, atom_feats, bond_feats)
                    labels = labels.unsqueeze(dim=1)
                    loss = loss_fn(y_pred, labels)
                    optimizer.zero_grad()
                    loss.backward()
                    optimizer.step()
                    epoch_loss += loss.detach().item()

                    if args.reg:
                        # Inverse transform to get RMSE
                        labels = y_scaler.inverse_transform(labels.cpu().reshape(-1, 1))
                        y_pred = y_scaler.inverse_transform(y_pred.detach().cpu().numpy().reshape(-1, 1))
                    else:
                        labels = labels.cpu().numpy()
                        y_pred = y_pred.detach().cpu().numpy()

                    # store labels and preds
                    preds.append(y_pred)
                    labs.append(labels)

                labs = np.concatenate(labs, axis=None)
                preds = np.concatenate(preds, axis=None)
                pearson, p = pearsonr(preds, labs)
                if args.reg:
                    mae = mean_absolute_error(preds, labs)
                    rmse = np.sqrt(mean_squared_error(preds, labs))
                    r2 = r2_score(preds, labs)
                else:
                    r2 = roc_auc_score(labs, preds)
                    precision, recall, thresholds = precision_recall_curve(labs, preds)
                    rmse = auc(recall, precision)
                    mae = 0

                if args.reg:
                    writer.add_scalar('Loss/train', epoch_loss, epoch)
                    writer.add_scalar('RMSE/train', rmse, epoch)
                    writer.add_scalar('R2/train', r2, epoch)
                else:
                    writer.add_scalar('Loss/train', epoch_loss, epoch)
                    writer.add_scalar('ROC-AUC/train', r2, epoch)
                    writer.add_scalar('PRC-AUC/train', rmse, epoch)

                if epoch % 20 == 0:
                    if args.reg:
                        print(f"epoch: {epoch}, "
                              f"LOSS: {epoch_loss:.3f}, "
                              f"RMSE: {rmse:.3f}, "
                              f"MAE: {mae:.3f}, "
                              f"rho: {pearson:.3f}, "
                              f"R2: {r2:.3f}")

                    else:
                        print(f"epoch: {epoch}, "
                              f"LOSS: {epoch_loss:.3f}, "
                              f"ROC-AUC: {r2:.3f}, "
                              f"PRC-AUC: {rmse:.3f}, "
                              f"rho: {pearson:.3f}")
                epoch_losses.append(epoch_loss)
                epoch_rmses.append(rmse)

            # Discount trial if train RMSE finishes as a negative value (optimiser error).

            if r2 < -1:
                skipped_trials += 1
                print('Skipped trials is {}'.format(skipped_trials))
                continue

            # Evaluate
            mpnn_net.eval()
            preds = []
            labs = []
            for i, (bg, labels) in enumerate(test_loader):
                labels = labels.to(device)
                atom_feats = bg.ndata.pop('h').to(device)
                bond_feats = bg.edata.pop('e').to(device)
                atom_feats, bond_feats, labels = atom_feats.to(device), bond_feats.to(device), labels.to(device)
                y_pred = mpnn_net(bg, atom_feats, bond_feats)
                labels = labels.unsqueeze(dim=1)

                if args.reg:
                    # Inverse transform to get RMSE
                    labels = y_scaler.inverse_transform(labels.cpu().reshape(-1, 1))
                    y_pred = y_scaler.inverse_transform(y_pred.detach().cpu().numpy().reshape(-1, 1))
                else:
                    labels = labels.cpu().numpy()
                    y_pred = y_pred.detach().cpu().numpy()
                preds.append(y_pred)
                labs.append(labels)

            labs = np.concatenate(labs, axis=None)
            preds = np.concatenate(preds, axis=None)
            pearson, p = pearsonr(preds, labs)
            if args.reg:
                mae = mean_absolute_error(preds, labs)
                rmse = np.sqrt(mean_squared_error(preds, labs))
                r2 = r2_score(preds, labs)
                writer.add_scalar('RMSE/test', rmse)
                writer.add_scalar('R2/test', r2)
                print(f'Test RMSE: {rmse:.3f}, MAE: {mae:.3f}, R: {pearson:.3f}, R2: {r2:.3f}')
            else:
                r2 = roc_auc_score(labs, preds)
                precision, recall, thresholds = precision_recall_curve(labs, preds)
                rmse = auc(recall, precision)
                mae = 0
                writer.add_scalar('ROC-AUC/test', r2)
                writer.add_scalar('PRC-AUC/test', rmse)
                print(f'Test ROC-AUC: {r2:.3f}, PRC-AUC: {rmse:.3f}, rho: {pearson:.3f}')

            r2_list.append(r2)
            rmse_list.append(rmse)
            mae_list.append(mae)
            j+=1

    r2_list = np.array(r2_list)
    rmse_list = np.array(rmse_list)
    mae_list = np.array(mae_list)
    if args.reg:
        print("\nmean R^2: {:.4f} +- {:.4f}".format(np.mean(r2_list), np.std(r2_list)/np.sqrt(len(r2_list))))
        print("mean RMSE: {:.4f} +- {:.4f}".format(np.mean(rmse_list), np.std(rmse_list)/np.sqrt(len(rmse_list))))
        print("mean MAE: {:.4f} +- {:.4f}\n".format(np.mean(mae_list), np.std(mae_list)/np.sqrt(len(mae_list))))
    else:
        print("mean ROC-AUC^2: {:.3f} +- {:.3f}".format(np.mean(r2_list), np.std(r2_list) / np.sqrt(len(r2_list))))
        print("mean PRC-AUC: {:.3f} +- {:.3f}".format(np.mean(rmse_list), np.std(rmse_list) / np.sqrt(len(rmse_list))))
    print("\nSkipped trials is {}".format(skipped_trials))


if __name__ == '__main__':

    parser = argparse.ArgumentParser()

    parser.add_argument('-task', '--task', type=str, default='e_iso_pi',
                        help='str specifying the task. One of [e_iso_pi, z_iso_pi, e_iso_n, z_iso_n].')
    parser.add_argument('-reg', action='store_true',
                        help='whether or not to do regression')
    parser.add_argument('-n', '--n_trials', type=int, default=3,
                        help='int specifying number of train/test splits to use')
    parser.add_argument('-n_fold', '--n_folds', type=int, default=3,
                        help='int specifying number of K-folds to use')
    parser.add_argument('-n_epochs', '--n_epochs', type=int, default=300,
                        help='int specifying number of epochs to train model')
    parser.add_argument('-ts', '--test_set_size', type=float, default=0.2,
                        help='float in range [0, 1] specifying fraction of dataset to use as test set')

    args = parser.parse_args()

    main(args)
