#Common imports
import os
import sys
import numpy as np
import argparse
import copy
import random
import json

#Pytorch
import torch
from torch.autograd import grad
from torch import nn, optim
from torch.nn import functional as F
from torchvision import datasets, transforms
from torchvision.utils import save_image
from torch.autograd import Variable
import torch.utils.data as data_utils

#Sklearn
from sklearn.manifold import TSNE

#robustdg
from utils.helper import *
from utils.match_function import *

# Input Parsing
parser = argparse.ArgumentParser()
parser.add_argument('--dataset_name', type=str, default='rot_mnist', help='Datasets: rot_mnist; fashion_mnist; pacs')
parser.add_argument('--method_name', type=str, default='erm_match', help=' Training Algorithm: erm_match; matchdg_ctr; matchdg_erm')
parser.add_argument('--model_name', type=str, default='resnet18')
parser.add_argument('--train_domains', type=int, default=["15", "30", "45", "60", "75"], help='List of train domains')
parser.add_argument('--test_domains', type=int, default=["0", "90"], help='List of test domains')
parser.add_argument('--out_classes', type=int, default=10, help='Total number of classes in the dataset')
parser.add_argument('--img_c', type=int, default= 1, help='Number of channels of the image in dataset')
parser.add_argument('--img_h', type=int, default= 224, help='Height of the image in dataset')
parser.add_argument('--img_w', type=int, default= 224, help='Width of the image in dataset')
parser.add_argument('--match_layer', type=str, default='logit_match', help='rep_match: Matching at an intermediate representation level; logit_match: Matching at the logit level')
parser.add_argument('--pos_metric', type=str, default='l2')
parser.add_argument('--rep_dim', type=int, default=250, help='Representation dimension for contrsative learning')
parser.add_argument('--pre_trained',type=int, default=0, help='0: No Pretrained Architecture; 1: Pretrained Architecture')
parser.add_argument('--perfect_match', type=int, default=1, help='0: No perfect match known (PACS); 1: perfect match known (MNIST)')
parser.add_argument('--opt', type=str, default='sgd', help='Optimizer Choice: sgd; adam') 
parser.add_argument('--lr', type=float, default=0.01, help='Learning rate for training the model')
parser.add_argument('--batch_size', type=int, default=64, help='Batch size foe training the model')
parser.add_argument('--epochs', type=int, default=15, help='Total number of epochs for training the model')
parser.add_argument('--penalty_w', type=float, default=0.0, help='Penalty weight for IRM invariant classifier loss')
parser.add_argument('--penalty_s', type=int, default=-1, help='Epoch threshold over which Matching Loss to be optimised')
parser.add_argument('--penalty_ws', type=float, default=0.1, help='Penalty weight for Matching Loss')
parser.add_argument('--penalty_diff_ctr',type=float, default=0.0)
parser.add_argument('--tau', type=float, default=0.05, help='Temperature hyper param for NTXent contrastive loss ')
parser.add_argument('--match_flag', type=int, default=0, help='0: No Update to Match Strategy; 1: Updates to Match Strategy')
parser.add_argument('--match_case', type=float, default=1.0, help='0: Random Match; 1: Perfect Match. 0.x" x% correct Match')
parser.add_argument('--match_interrupt', type=int, default=5, help='Number of epochs before inferring the match strategy')
parser.add_argument('--test_metric', type=str, default='acc', help='Evaluation Metrics: acc; match_score')
parser.add_argument('--top_k', type=int, default=10, help='Top K matches to consider for the match score evaluation metric')
parser.add_argument('--ctr_abl', type=int, default=0, help='0: Randomization til class level ; 1: Randomization completely')
parser.add_argument('--match_abl', type=int, default=0, help='0: Randomization til class level ; 1: Randomization completely')
parser.add_argument('--n_runs', type=int, default=3, help='Number of iterations to repeat the training process')
parser.add_argument('--n_runs_matchdg_erm', type=int, default=2)
parser.add_argument('--mnist_seed', type=int, default=0, help='Change it between 0-6 for different subsets of Mnist and Fashion Mnist dataset')
parser.add_argument('--retain', type=float, default=0, help='0: Train from scratch in MatchDG Phase 2; 2: Finetune from MatchDG Phase 1 in MatchDG is Phase 2')
parser.add_argument('--cuda_device', type=int, default=0, help='Select the cuda device by id among the avaliable devices' )
args = parser.parse_args()

#GPU
cuda= torch.device("cuda:" + str(args.cuda_device))
if cuda:
    kwargs = {'num_workers': 1, 'pin_memory': False} 
else:
    kwargs= {}

#List of Train; Test domains
train_domains= args.train_domains
test_domains= args.test_domains

#Initialize
final_report_accuracy=[]
match_acc=[]
match_rank=[]
match_top_k=[]
final_acc=[]
val_acc=[]
base_res_dir="results/" + args.dataset_name + '/' + args.method_name + '/' + args.match_layer + '/' + 'train_' + str(args.train_domains) + '_test_' + str(args.test_domains) 
if not os.path.exists(base_res_dir):
    os.makedirs(base_res_dir)    

#Execute the method for multiple runs ( total args.n_runs )
for run in range(args.n_runs):
    
    #Seed for repoduability
    torch.manual_seed(run*10)    
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(run*10)    

    #DataLoader        
    train_dataset, val_dataset, test_dataset, total_domains, domain_size, training_list_size= get_dataloader( args, run, train_domains, test_domains, kwargs )
    print('Train Domains, Domain Size, BaseDomainIdx, Total Domains: ', train_domains, total_domains, domain_size, training_list_size)
    
    #Import the testing module
    if args.test_metric == 'acc':
        from evaluation.base_eval import BaseEval
        test_method= BaseEval(args, train_dataset, test_dataset, train_domains, total_domains, domain_size, training_list_size, cuda)
    elif args.test_metric == 'match_score':
        from evaluation.match_eval import MatchEval
        test_method= MatchEval(args, train_dataset, test_dataset, train_domains, total_domains, domain_size, training_list_size, cuda)   
    
    #Path to load model       
    post_string= str(args.penalty_ws) + '_' + str(args.penalty_diff_ctr) + '_' + str(args.rep_dim) + '_' + str(args.match_case) + '_' + str(args.match_interrupt) + '_' + str(args.match_flag) + '_' + str(run) + '_' + args.pos_metric + '_' + args.model_name    
    test_method.phi.load_state_dict( torch.load(base_res_dir + '/Model_' + post_string + '.pth') )
    test_method.phi.eval()
    
    #Test the method
    
    #Testing Phase
    if args.test_metric == 'acc':
        test_method.get_eval_acc('Test')
        
        ## TODO: Should the check of matchdg_ctr be present for evaluating final test accuracy
        # Final Report Accuacy
        if args.method_name != 'matchdg_ctr':
            final_report_accuracy.append( test_method.final_acc )
        
    elif args.test_metric == 'match_score':
        test_method.get_match_score(args.top_k)
        
        ##TODO: Is Perfect Match Check really needed here?
        if args.perfect_match:
            match_acc.append( test_method.match_top_1 )
            match_rank.append( test_method.match_rank )
            match_top_k.append( test_method.match_top_k )
    

print('\n')
print('Done for Model..')

if args.test_metric == 'acc':
    print('Test Accuracy', np.mean(final_report_accuracy), np.std(final_report_accuracy) )
    
elif args.test_metric == 'match_score':
    print('Perfect Match Score: ',  np.mean(match_acc), np.std(match_acc)  )                    
    print('TopK Perfect Match Score: ',  np.mean(match_top_k), np.std(match_top_k)  )            
    print('Perfect Match Rank: ',  np.mean(match_rank), np.std(match_rank) )            
    
print('\n')