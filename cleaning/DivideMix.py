import sys
import os
from pathlib import Path

CURR_FILE_PATH = (os.path.abspath(__file__))
PATH = Path(CURR_FILE_PATH)
CURR_DIR = str(PATH.parent.absolute())

sys.path.append(CURR_DIR)
P = PATH.parent
print("current dir: ",CURR_DIR)
for i in range(1):  
    P = P.parent
    PROJECT_PATH = str(P.absolute())
    sys.path.append(str(P.absolute()))
import time
TIME=time.strftime("%m%d%H%M%S", time.localtime())
print("time",TIME)
from utils.dict_relate import dict_index
from utils.pickle_picky import load, save
from utils.randomness import set_global_random_seed
from model.sccl import SCCL_BERT
from sentence_transformers import SentenceTransformer
from torch.utils.data import Dataset, DataLoader
from sklearn.mixture import GaussianMixture
import copy
import torch.optim as optim
import torch.nn.functional as F
import torch.nn as nn
import torch
import matplotlib.pyplot as plt
from torch.autograd import Variable
import argparse
import torch.utils.data as util_data
import numpy as np
import random
from tqdm import tqdm
from collections import Counter
import re
from torchnet.meter import AUCMeter
from utils.loss_utils import Augmentation























def create_model(args):
    tags = load(args.e_tags_path)
    bert_model  = SentenceTransformer(args.model_dir)
    model = SCCL_BERT(bert_model,args.max_len,args.device,args.n_rel,True,tags).to(args.device)
    return model

def linear_rampup(args,current, warm_up, rampup_length=16):
    current = np.clip((current-warm_up) / rampup_length, 0.0, 1.0)
    return args.lambda_u*float(current)

class SemiLoss(object):
    def __call__(self,args, outputs_x, targets_x, outputs_u, targets_u, epoch, warm_up):
        probs_u = torch.softmax(outputs_u, dim=1)

        Lx = -torch.mean(torch.sum(F.log_softmax(outputs_x, dim=1) * targets_x, dim=1))  
        Lu = torch.mean((probs_u - targets_u)**2)

        return Lx, Lu, linear_rampup(args,epoch,warm_up)

class Mydataset(Dataset): 
    def __init__(self,args, mode,augmentation:Augmentation, pred=[], probability=[], log=''): 
        self.aug = augmentation
        self.mode = mode 
        self.args = args
        if self.mode == "test":
            dataset = load(args.test_path)
            if args.dataset=="tac":
                pos_index = [i for i in range(len(dataset['label'])) if dataset['label'][i]!=41]
                dataset = dict_index(dataset,pos_index)
            self.test_data = dataset['text']
            self.test_label = dataset['label']
        else:
            dataset = load(args.train_path)
            self.train_data = dataset['text']
            self.noise_label = dataset['top1']
            if self.mode == 'all':
                pass  
            else:  
                if self.mode == "labeled":
                    pred_idx = pred.nonzero()[0]
                    self.probability = [probability[i] for i in pred_idx]             
                    
                elif self.mode == "unlabeled":
                    pred_idx = (1-pred).nonzero()[0]     
                
                self.train_data = [self.train_data[i] for i in pred_idx]   
                self.noise_label = [self.noise_label[i] for i in pred_idx]         
                print("%s data has a size of %d"%(self.mode,len(self.noise_label)))   
    def __getitem__(self, index):
        if self.mode=='labeled':
            text1, target, prob = self.train_data[index], self.noise_label[index], self.probability[index]
            
            text2 = self.aug.get_aug(text1)[0]
            return text1, text2, target, prob , index           
        elif self.mode=='unlabeled':
            text1 = self.train_data[index]
            
            text2 = self.aug.get_aug(text1)[0]
            return text1, text2, index   
        elif self.mode=='all':
            text1, target = self.train_data[index], self.noise_label[index]
            return text1, target, index        
        elif self.mode=="test" : 
            text1, target = self.test_data[index], self.test_label[index]
            return text1, target

    def __len__(self):
        if self.mode!='test':
            return len(self.train_data)
        else:
            return len(self.test_data)     

class MyDataLoader():
    def __init__(self,args, batch_size, num_workers,augmentation) -> None:
        self.args = args  
        self.batch_size = batch_size
        self.num_workers = num_workers
        self.augmentation = augmentation
    def run(self,mode,pred=[],prob=[]):
        if mode=='warmup':
            all_dataset = Mydataset(self.args,mode="all",augmentation=self.augmentation)
            trainloader = DataLoader(
                dataset=all_dataset, 
                batch_size=self.batch_size*2,
                shuffle=True,
                num_workers=self.num_workers)             
            return trainloader
        elif mode=='train':
            labeled_dataset = Mydataset(self.args,  mode="labeled", pred=pred, probability=prob,augmentation=self.augmentation)         
            labeled_trainloader = DataLoader(
                dataset=labeled_dataset, 
                batch_size=self.batch_size,
                shuffle=True,
                num_workers=self.num_workers)  
            unlabeled_dataset = Mydataset(self.args, mode="unlabeled",  pred=pred,augmentation=self.augmentation)                    
            unlabeled_trainloader = DataLoader(
                dataset=unlabeled_dataset, 
                batch_size=self.batch_size,
                shuffle=True,
                num_workers=self.num_workers)     
            return labeled_trainloader, unlabeled_trainloader 

        elif mode=='test':
            test_dataset = Mydataset(self.args,  mode='test',augmentation=self.augmentation)      
            test_loader = DataLoader(
                dataset=test_dataset, 
                batch_size=self.batch_size,
                shuffle=False,
                num_workers=self.num_workers)          
            return test_loader
        
        elif mode=='eval_train':
            eval_dataset = Mydataset(self.args,  mode='all',augmentation=self.augmentation)      
            eval_loader = DataLoader(
                dataset=eval_dataset, 
                batch_size=self.batch_size,
                shuffle=False,
                num_workers=self.num_workers)          
            return eval_loader        
           
            
def warmup(args,epoch,net:SCCL_BERT,optimizer,dataloader,CEloss):
    net.train()
    num_iter = len(dataloader)
    for batch_idx, (texts, labels,index) in enumerate(dataloader):   
        optimizer.zero_grad()
        labels = labels.to(args.device)
        outputs = net(list(texts))
        loss = CEloss(outputs, labels)
        loss.backward()  
        optimizer.step() 
        sys.stdout.write('\r')
        sys.stdout.write('%s: | Epoch [%3d/%3d] Iter[%3d/%3d]\t CE-loss: %.4f'
                %(args.dataset, epoch, args.epoch, batch_idx+1, num_iter, loss.item()))
        sys.stdout.flush()

def test(epoch,net1:SCCL_BERT,net2:SCCL_BERT,test_loader):
    net1.eval()
    net2.eval()
    correct = 0
    total = 0
    with torch.no_grad():
        for batch_idx, (texts, targets) in enumerate(test_loader):
            targets = targets.to(args.device)
            outputs1 = net1(list(texts))
            outputs2 = net2(list(texts))
            outputs = outputs1+outputs2
            _, predicted = torch.max(outputs, 1)            
                       
            total += targets.size(0)
            correct += predicted.eq(targets).cpu().sum().item()                 
    acc = 100.*correct/total
    print("\n| Test Epoch #%d\t Accuracy: %.2f%%\n" %(epoch,acc))  

def eval_train(args,model:SCCL_BERT,all_loss,eval_loader,CE):    
    model.eval()
    losses = torch.zeros(args.N_train)  
    data_predict = torch.zeros(args.N_train,dtype=torch.long) 
    data_predict_confidence = torch.zeros(args.N_train) 
    with torch.no_grad():
        for batch_idx, (texts, targets, index) in enumerate(eval_loader):
            targets = targets.to(args.device)
            outputs = model(list(texts))
            loss = CE(outputs, targets)  
            _, pred = torch.max(outputs.data, -1)  
            
            
            confidences = F.softmax(outputs,-1)
            confidence = confidences[np.array([i for i in range(len(outputs))]),pred]
            data_predict[index] = pred.detach().cpu() 
            data_predict_confidence[index] = confidence.detach().cpu() 
            stop = 1
            
            for b in range(outputs.size(0)):  
                losses[index[b]]=loss[b]         
    losses = (losses-losses.min())/(losses.max()-losses.min())    
    all_loss.append(losses)

    input_loss = losses.reshape(-1,1)
    
    
    gmm = GaussianMixture(n_components=2,max_iter=10,tol=1e-2,reg_covar=5e-4)
    gmm.fit(input_loss)
    prob = gmm.predict_proba(input_loss) 
    prob = prob[:,gmm.means_.argmin()]      
    return prob,all_loss,data_predict,data_predict_confidence

def train(args,epoch,net:SCCL_BERT,net2:SCCL_BERT,optimizer,labeled_trainloader,unlabeled_trainloader,criterion:SemiLoss,warm_up):
    net.train()
    net2.eval() 
    unlabeled_train_iter = iter(unlabeled_trainloader) 
    num_iter = len(labeled_trainloader)

    for batch_idx, (texts_x, texts_x2, labels_x, w_x,index) in enumerate(labeled_trainloader): 
        try:
            texts_u, texts_u2,u_index = unlabeled_train_iter.next()  
        except:
            unlabeled_train_iter = iter(unlabeled_trainloader)
            texts_u, texts_u2,u_index = unlabeled_train_iter.next()                 
        batch_size = len(texts_x)

        labels_x = torch.zeros(batch_size, args.n_rel).scatter_(1, labels_x.view(-1,1), 1)        
        w_x = w_x.view(-1,1).type(torch.FloatTensor)

        labels_x, w_x =  labels_x.to(args.device), w_x.to(args.device)

        with torch.no_grad():
            outputs_u11 = net(list(texts_u))
            outputs_u12 = net(list(texts_u2))
            outputs_u21 = net2(list(texts_u))
            outputs_u22 = net2(list(texts_u2))

            pu = (torch.softmax(outputs_u11, dim=1) + torch.softmax(outputs_u12, dim=1) + torch.softmax(outputs_u21, dim=1) + torch.softmax(outputs_u22, dim=1)) / 4       
            ptu = pu**(1/args.T) 
            
            targets_u = ptu / ptu.sum(dim=1, keepdim=True) 
            targets_u = targets_u.detach()       
            
            
            outputs_x = net(list(texts_x))  
            outputs_x2 = net(list(texts_x2))
            
            px = (torch.softmax(outputs_x, dim=1) + torch.softmax(outputs_x2, dim=1)) / 2
            px = w_x*labels_x + (1-w_x)*px              
            ptx = px**(1/args.T) 
                       
            targets_x = ptx / ptx.sum(dim=1, keepdim=True) 
            targets_x = targets_x.detach()    




        l = np.random.beta(args.alpha, args.alpha)        
        l = max(l, 1-l)
        all_inputs = texts_x+ texts_x2+ texts_u+ texts_u2
        all_inputs = net.get_embeddings_PURE(list(all_inputs))
        all_targets = torch.cat([targets_x, targets_x, targets_u, targets_u], dim=0)

        idx = torch.randperm(len(all_inputs))   

        input_a, input_b = all_inputs, all_inputs[idx]  
        target_a, target_b = all_targets, all_targets[idx]
        
        mixed_input = l * input_a + (1 - l) * input_b        
        
        mixed_target = l * target_a + (1 - l) * target_b
                
        logits = net.out(mixed_input)
        logits_x = logits[:batch_size*2]
        logits_u = logits[batch_size*2:]        
        
        Lx, Lu, lamb = criterion(args,logits_x, mixed_target[:batch_size*2], logits_u, mixed_target[batch_size*2:], epoch+batch_idx/num_iter, warm_up)
        
        
        prior = torch.ones(args.n_rel)/args.n_rel
        prior = prior.to(args.device)      
        pred_mean = torch.softmax(logits, dim=1).mean(0)
        penalty = torch.sum(prior*torch.log(prior/pred_mean))

        loss = Lx + lamb * Lu  + penalty
        
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        sys.stdout.write('\r')
        sys.stdout.write('Trian %s: | Epoch [%3d/%3d] Iter[%3d/%3d]\t CE-loss: %.4f'
                %(args.dataset, epoch, args.epoch, batch_idx+1, num_iter, loss.item()))
        sys.stdout.flush()



def dividemix_main(args):
    set_global_random_seed(args.seed)
    args.device = torch.device("cuda:{}".format(args.cuda_index))
    args.dataset = "tac" if not args.train_path.find("wiki")!=-1 else "wiki"
    train_data = load(args.train_path)
    train_data['index'] = [i for i in range(len(train_data['text']))]  
    
    
    print("*"*10,"information","*"*10)
    info_acc = sum(np.array(train_data['top1'])==np.array(train_data['label']))/len(train_data['top1'])
    n_pseudo_label_relation = len(set(train_data['top1']))
    print("acc:{:.4f}".format(info_acc))
    print("n_relation:{}".format(n_pseudo_label_relation))
    print("N_data:{}".format(len(train_data['top1'])))
    print("*"*10,"***********","*"*10)
    

    clean_index = []
    noisy_index = []
    for i,(gt, top1) in enumerate(zip(train_data['label'],train_data['top1'])):
        if gt== top1:
            clean_index.append(i)
        else:
            noisy_index.append(i)
    clean_index = np.array(clean_index)
    noisy_index = np.array(noisy_index)

    Aug = Augmentation(train_data)
    args.N_train = len(train_data['text'])
    warm_up = 5
    net1:SCCL_BERT = create_model(args)
    net2:SCCL_BERT = create_model(args)
    criterion = SemiLoss()  
    optimizer1 = torch.optim.AdamW([
        {'params': net1.sentbert.parameters()},
        {'params': net1.out.parameters(), 'lr': args.lr*args.lr_scale}], lr=args.lr)
    optimizer2 = torch.optim.AdamW([
        {'params': net2.sentbert.parameters()},
        {'params': net2.out.parameters(), 'lr': args.lr*args.lr_scale}], lr=args.lr)
    CE = nn.CrossEntropyLoss(reduction='none')
    CEloss = nn.CrossEntropyLoss()
    all_loss = [[],[]]
    loader = MyDataLoader(args,args.batch_size,num_workers=0,augmentation=Aug)
    
    for epoch in range(args.epoch):   
        
        eval_loader = loader.run('eval_train')
        if epoch<warm_up: 
            warmup_trainloader = loader.run('warmup') 
            print('Warmup Net1')
            warmup(args,epoch,net1,optimizer1,warmup_trainloader,CEloss)    
            print('\nWarmup Net2')
            warmup(args,epoch,net2,optimizer2,warmup_trainloader,CEloss) 
        else:         
            prob1,all_loss[0],data_predict1,data_predict_confidence1=eval_train(args,net1,all_loss[0],eval_loader,CE)   
            prob2,all_loss[1],data_predict2,data_predict_confidence2=eval_train(args,net2,all_loss[1],eval_loader,CE)    
            if random.random()<0.5:
                data_predict, data_predict_confidence = data_predict1,data_predict_confidence1
            else: data_predict, data_predict_confidence = data_predict2,data_predict_confidence2

            
            
            predicts = dict()
            for i,(pre,confi) in enumerate(zip(data_predict,data_predict_confidence)):
                pre = pre.item()
                confi = confi.item()
                if pre not in predicts:
                    predicts[pre] = [(i,confi)] 
                else:
                    predicts[pre].append((i,confi))
            max_num = -1
            for k in predicts.keys():  
                predicts[k].sort(key=lambda x : x[1],reverse=True)
                L = len(predicts[k])
                if L>max_num:
                    max_num = L
            
            cate_confidence_sorted = []
            for i in range(max_num):
                for k in predicts.keys():
                    if i<len(predicts[k]):
                        cate_confidence_sorted.append(predicts[k][i])
            few_shot_nums = np.array([1,2,3])*42 if args.dataset=="tac" else np.array([1,2,3])*80
            k = 1
            for few_short_num in few_shot_nums:
                selected_index = [item[0] for item in cate_confidence_sorted[:few_short_num]]
                selected_data = dict_index(train_data,selected_index)
                selected_data['top1'] = selected_data['p_label'] = selected_data['label'] 
                selected_label = [train_data['label'][i] for i in selected_index]
                print("TAC fewshot: total",len(Counter(selected_label)),"=>",Counter(selected_label))
                print("len data:",len(selected_data['text']))
                save(selected_data,"/home/tywang/URE-master/scripts/fewshot_33lab/tac_confi_select_DivideMix_k{}_epoch{}.pkl".format(k,epoch))
                k+=1
            
            
            
            
            
            
            
            
            
            
            
            
            
            
            pred1 = (prob1 > args.p_threshold)      
            pred2 = (prob2 > args.p_threshold)      
            
            print('Train Net1')
            labeled_trainloader, unlabeled_trainloader = loader.run('train',pred2,prob2) 
            train(args,epoch,net1,net2,optimizer1,labeled_trainloader, unlabeled_trainloader,criterion,warm_up) 
            
            print('\nTrain Net2')
            labeled_trainloader, unlabeled_trainloader = loader.run('train',pred1,prob1) 
            train(args,epoch,net2,net1,optimizer2,labeled_trainloader, unlabeled_trainloader,criterion,warm_up) 
            

            
            all_loss_mean = (torch.stack(all_loss[0])+torch.stack(all_loss[1]))/2
            plot_loss = all_loss_mean[:].mean(0)
            sorted_loss = np.argsort(plot_loss).numpy()
            
            indexes = []
            ratios = [0.01,0.02,0.025,0.03,0.04,0.05,0.06,0.07,0.075,0.08,0.09,0.1]
            if args.dataset=="wiki":
                n_train = 40320
                
            else:
                n_train = 68124
                
            select_num = [int(rt*n_train) for rt in ratios]
            accs = []
            for select_n,rt in zip(select_num,ratios):
                selected  = sorted_loss[:int(select_n)]
                indexes.append(selected)
                Slabel = np.array([train_data['label'][index] for index in selected])
                Sp_label = np.array([train_data['top1'][index] for index in selected])
                n_cate = len(set(Sp_label))
                acc = sum(Slabel==Sp_label)/len(Sp_label)
                
                accs.append(acc)
                print("前{} {} confidence 大的数据 acc= {}, 类别数:{}".format(rt,select_n,acc,n_cate))
            
            if epoch+1==args.epoch:
                
                ratio = [0.01,0.02,0.025,0.03,0.04,0.05,0.06,0.07,0.075,0.08,0.09,0.1]
                for index,rt in zip(indexes[:len(ratio)],ratio):
                    selected_data = dict_index(train_data,index)
                    acc = sum(np.array(selected_data['label'])==np.array(selected_data['top1']))/len(selected_data['label'])
                    print("selected data acc:{}, num:{}".format(acc,len(selected_data['text'])))
                    
                    
                    if args.specified_save_path != "": 
                        
                        
                        save(selected_data,os.path.join(args.specified_save_path,"DivideMix_{}_RT{}_SD{}.pkl".format(args.dataset,rt,args.seed)))
                    else:
                        save(selected_data,"/home/tywang/URE-master/scripts/fewshot_cleaned_data/DivideMix_oriNum{}_n{}train_acc_WIKI_DMix_K3.pkl".format(
                            args.N_train,rt
                        ))
                    
                loss_plot = (torch.stack(all_loss[0])+torch.stack(all_loss[1]))/2
                loss_plot = loss_plot.mean(0)
                metric = {
                "noisy_metric":loss_plot,
                "clean_index":clean_index,
                "noisy_index":noisy_index
                }
                


        
    

if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=18, help="as named")
    parser.add_argument("--cuda_index", type=int, default=0, help="as named")
    parser.add_argument('--lr', type=float, default=4e-7, help='learning rate')
    parser.add_argument('--lr_scale', type=int, default=100, help='as named')
    parser.add_argument('--epoch', type=int, default=10, help='as named')
    parser.add_argument('--batch_size', type=int, default=64, help='as named')
    parser.add_argument('--T', default=0.5, type=float, help='sharpening temperature')
    parser.add_argument('--alpha', default=4, type=float, help='parameter for Beta')
    parser.add_argument('--p_threshold', default=0.5, type=float, help='clean probability threshold')
    parser.add_argument('--lambda_u', default=25, type=float, help='weight for unsupervised loss')

    
    parser.add_argument("--train_path", type=str,
                        default="", help="as named")
    parser.add_argument('--n_rel', type=int, default=0, help='as named')
    parser.add_argument("--test_path", type=str,
                        default="", help="as named")
    parser.add_argument("--e_tags_path", type=str,
                        default="train_tags.pkl", help="as named")
    
    

    """communal"""

    parser.add_argument('--save_info', type=str,
                        default="", help='as named')
    parser.add_argument('--model_dir', type=str,
                        default='/data/transformers/bert-base-uncased', help='as named')
    parser.add_argument('--max_len', type=int, default=64,
                        help='length of input sentence')
    parser.add_argument("--specified_save_path", type=str,default="", help="as named")
    args = parser.parse_args()


    
    

    dividemix_main(args)
    print(time.strftime("%m%d%H%M%S", time.localtime()))