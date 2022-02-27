import sys
import os
from pathlib import Path

CURR_FILE_PATH = (os.path.abspath(__file__))
PATH = Path(CURR_FILE_PATH)
CURR_DIR = str(PATH.parent.absolute())

sys.path.append(CURR_DIR)
P = PATH.parent
print("current dir: ",CURR_DIR)
for i in range(1):  # add parent path, height = 3
    P = P.parent
    PROJECT_PATH = str(P.absolute())
    sys.path.append(str(P.absolute()))


from dataclasses import dataclass
import random
import json
from collections import defaultdict
from utils.pickle_picky import load, save
from utils.randomness import set_global_random_seed
import argparse
from tqdm import tqdm 
import copy
from collections import Counter
import numpy as np
import json

@dataclass
class MNLIInputFeatures:
    premise: str
    hypothesis: str
    label: int
    # {0: 'CONTRADICTION', 1: 'NEUTRAL', 2: 'ENTAILMENT'}
labels2id = {"entailment": 2, "neutral": 1, "contradiction": 0}


def tacred2mnli_neutral(
    args,
    context,
    subj,
    obj,
    label, # p_label
    positive_templates,
    sorted_template,
    template2label,
    negn=1,
    posn=1,
):
    """
        pos:
            entailment: 自己的template
            neutral: 别的pos的template
            contraction:  a and b has no relation 模板
        neg:
            entailment: 自己的template
            neutral: 无
            contraction: 使用pos template

            new
            entailment: 自己的template
            neutral: 使用pos template(任何pos template)
            contraction: There is a relation between {subj} and {obj} .

    """

    mnli_instances = []

    # 生成entailment的label
    positive_template = random.choices(positive_templates[label], k=posn)  # 取出relation对应的模板
    mnli_instances.extend(  # 用这个rel对应的pos_template 来陈述 MNLIInputFeatures
        [
            MNLIInputFeatures(
                premise=context,
                hypothesis=f"{t.format(subj=subj, obj=obj)}.",
                label=labels2id["entailment"],
            )
            for t in positive_template  
        ]
    )
    # 生成neutral的label
    # pos_data用别的的relation的模板就是neutral,  no_relation用pos_模板就是contraction
    # negative_template = random.choices(negative_templates[label], k=negn)  # 找对应这个rel的negative template, 随机选一个
    
    # 新的select neutral label
    canbe_selected_sorted_template = []
    for neg_template in sorted_template:
        if neg_template=='{subj} and {obj} are not related':
            continue
        neg_template_label = template2label[neg_template]
        if label not in neg_template_label:
            canbe_selected_sorted_template.append(neg_template)
    
    if args.random:
        negative_template = random.choices(canbe_selected_sorted_template, k=negn)
    else:
        negative_template = canbe_selected_sorted_template[:negn] #canbe_selected_sorted_template[:negn]
    mnli_instances.extend(
        [
            MNLIInputFeatures(
                premise=context,
                hypothesis=f"{t.format(subj=subj, obj=obj)}.",
                # label=labels2id["neutral"] if label != "no_relation" else labels2id["contradiction"],
                label=labels2id["neutral"],
            )
            for t in negative_template
        ]
    )

    # contradiction的label
    # pos数据使用 no_relation 模板就是contraction
    if label != "no_relation":
        mnli_instances.append(
            MNLIInputFeatures(
                premise=context,
                hypothesis="{subj} and {obj} are not related.".format(subj=subj, obj=obj),
                label=labels2id["contradiction"],
            )
        )
    
    # neg的 contradiction 用 there is a relation模板
    if label == "no_relation": 
        mnli_instances.append(
            MNLIInputFeatures(
                premise=context,
                hypothesis="There is a relation between {subj} and {obj}.".format(subj=subj, obj=obj),
                label=labels2id["contradiction"],
            )
        )
    return mnli_instances


def get_mnli_data(args,data,positive_templates,negative_templates):
    # e_tags = load(args.e_tags_path)
    
    #assert data['p_label']==data['label']  # 确保p_label选用的是top1
    label2id = load(args.label2id_path)
    id2label = dict(zip(list(label2id.values()),list(label2id.keys())))
    template2label:dict = load(args.template2label_path)
    # for key,relations in template2id.items():
    #     template2id[key] = [label2id[item] for item in relations]

    mnli_data = []
    assert data['p_label']==data['top1']
    for context, subj, obj, p_label, sorted_template in tqdm(zip(data['text'],data['subj'],data['obj'],data['p_label'],data['template']),total=len(data['text'])):
        # for tags in e_tags: # 去除e_tags
        #     context = context.replace(tags,"")
        context=context.replace("-LRB-", "(").replace("-RRB-", ")").replace("-LSB-", "[").replace("-RSB-", "]")
        subj=subj.replace("-LRB-", "(").replace("-RRB-", ")").replace("-LSB-", "[").replace("-RSB-", "]")
        obj=obj.replace("-LRB-", "(").replace("-RRB-", ")").replace("-LSB-", "[").replace("-RSB-", "]")
        # mnli_instance = tacred2mnli(
        #     context=context,
        #     subj=subj,
        #     obj=obj,
        #     label=id2label[p_label],
        #     positive_templates=positive_templates,
        #     negative_templates=negative_templates
        # )
        mnli_instance = tacred2mnli_neutral(
            args = args,
            context=context,
            subj=subj,
            obj=obj,
            label=id2label[p_label],
            positive_templates=positive_templates,
            sorted_template=sorted_template,
            template2label = template2label
        )
        mnli_data.extend(mnli_instance) 
    mnli_labels = [item.label for item in mnli_data]
    dispersion_dict = dict(Counter(mnli_labels))
    # {0: 'CONTRADICTION', 1: 'NEUTRAL', 2: 'ENTAILMENT'}
    print("mnli trian data dispersion:",dispersion_dict)
    args.MNLI_ratio = "{}{}{}".format(dispersion_dict[0]//dispersion_dict[0],
    dispersion_dict[1]//dispersion_dict[0],dispersion_dict[2]//dispersion_dict[0])
    return mnli_data

def get_tac_pos_neg_templates(config_path:str):
    # 每个类挑一个template(每个类通常会有多个template), 然后取set
    
    if config_path.find("wiki")!=-1: #wiki
        with open(config_path) as file:
            data_dict = json.load(file)
        template_dict = data_dict[0]['template_mapping']
        templates = []
        for k in template_dict.keys():
            templates.append(template_dict[k][0])
    else:
        # Choose a template for each relation in TACRED, and remove duplicate templates(cause one may be shared by multiple relatoins) 
        templates = [
        "{subj} and {obj} are not related",
        "{subj} is also known as {obj}",
        "{subj} was born in {obj}",
        "{subj} is {obj} years old",
        "{obj} is the nationality of {subj}",
        "{subj} died in {obj}",
        "{obj} is the cause of {subj}'s death",
        "{subj} lives in {obj}",
        "{subj} studied in {obj}",
        "{subj} is a {obj}",
        "{subj} is an employee of {obj}",
        "{subj} believe in {obj}",
        "{subj} is the spouse of {obj}",
        "{subj} is the parent of {obj}",
        "{obj} is the parent of {subj}",
        "{subj} and {obj} are siblings",
        "{subj} and {obj} are family",
        "{subj} was convicted of {obj}",
        "{subj} has political affiliation with {obj}",
        "{obj} is a high level member of {subj}",
        "{subj} has about {obj} employees",
        "{obj} is member of {subj}",
        "{subj} is member of {obj}",
        "{obj} is a branch of {subj}",
        "{subj} is a branch of {obj}",
        "{subj} was founded by {obj}",
        "{subj} was founded in {obj}",
        "{subj} existed until {obj}",
        "{subj} has its headquarters in {obj}",
        "{obj} holds shares in {subj}",
        "{obj} is the website of {subj}",
        ]

    with open(config_path, "rt") as f:
        config = json.load(f)[0]
    LABEL_TEMPLATES = config['template_mapping']
    LABELS = config['labels']
    positive_templates = defaultdict(list)
    negative_templates = defaultdict(list)

    for label in LABELS:
        for template in templates: # 遍历所有的template
            if label != "no_relation" and template == "{subj} and {obj} are not related":
                continue
            if template in LABEL_TEMPLATES[label]:
                positive_templates[label].append(template)
            else:
                negative_templates[label].append(template)
    return positive_templates,negative_templates

def tacred2mnli_main(args):
    """
        要求的key:
        data['text'],data['subj'],data['obj'],data['p_label'])
    """
    prepare_train_data(args)
    config_path = args.config_path
    positive_templates,negative_templates = get_tac_pos_neg_templates(config_path)
    data = load(args.data_path)
    mnli_data = get_mnli_data(args,data,positive_templates=positive_templates,
                                    negative_templates=negative_templates)
    random.shuffle(mnli_data)
    save(mnli_data,"/home/tywang/myURE/URE/fine_tune/data/tac_num681_only_neg_acc0.98_for_finetune_mnli.pkl")
    return mnli_data


def from_selected(args):
    """
        直接从select出来的数据来进行得到mnli数据
    """
    selected_data = load(args.selected_data_path)
    n_selected_data = len(selected_data['text'])
    whole_data = prepare_data(args,selected_data)
    config_path = args.config_path
    positive_templates,negative_templates = get_tac_pos_neg_templates(config_path)
    mnli_data = get_mnli_data(args,whole_data,positive_templates=positive_templates,
                                    negative_templates=negative_templates)
    random.shuffle(mnli_data)
    if config_path.find("wiki")!=-1:
        mode = "wiki"
    else: mode="tac"
    save_path = os.path.join(CURR_DIR,"finetune_data/{}/finetune_n{}train.pkl".format(mode,args.ratio))
    # save_path = os.path.join(CURR_DIR,"{}_num{}_acc{:.4f}_for_finetune_mnli_fewShot.pkl".format(mode,n_selected_data,args.acc))

    save(mnli_data,save_path)
    

def prepare_data(args,data):
    label2id = load(args.label2id_path)
    whole_data = copy.deepcopy(data)
    ##
    # Make sure rel and label are corresponding and can be converted to each other by label2id
    rel_id = [label2id[item] for item in whole_data['rel']]
    assert rel_id==whole_data['label']  # 保证label没错, 避免label误换成top1
    ##
    clean_texts = []
    for idx,text in enumerate(whole_data['text']):
        text = text.split()
        temp_text = []
        for word in text:
            word:str
            if not word.startswith(("</O:","<O:","</S:","<S:")): # eliminate tags like <O:OBJECT_TYPE>
                temp_text.append(word)
        clean_texts.append(' '.join(temp_text))
    whole_data['text'] = clean_texts
    whole_data['p_label'] = copy.deepcopy(whole_data['top1'])  # Make sure pseudo label is top1 prediction
    args.acc = sum(np.array(whole_data['p_label'])==np.array(whole_data['label']))/len(whole_data['p_label'])
    
    # gt_not_neg_index = [i for i,item in enumerate(whole_data['label']) if item!=41 ]
    # p_label = [whole_data['top1'][i] for i in gt_not_neg_index]
    # Label = [whole_data['label'][i] for i in gt_not_neg_index]
    # print("top1 acc:",sum(np.array(whole_data['top1'])==np.array(whole_data['label']))/len(whole_data['top1']))
    print("label 分布:",Counter(whole_data['label']))
    print("p_label 分布:",Counter(whole_data['p_label']))
    # print("label 中neg占比 :",sum(np.array(whole_data['label'])==41)/len(whole_data['label']))
    # print("top1 中neg占比",sum(np.array(whole_data['top1'])==41)/len(whole_data['top1']))
    print("top 1acc:",args.acc)

    # save(whole_data,"/data/tywang/0.0ktrain/selectedFromO2u/top_681_41rels_acc0.8473.pkl")

    return whole_data
    


def prepare_train_data(args):
    data_1 = load("/home/tywang/myURE/URE/fine_tune/data/top_681_1rels_0.98.pkl")
    # data_41 = load("/home/tywang/myURE/URE/fine_tune/data/top_681_41rels_0.82.pkl") #用index选出来的数据
    # del data_41['noise_or_not']
    # assert data_1.keys()==data_41.keys()
    # whole_data = copy.deepcopy(data_1)
    # for k in data_1.keys():
    #     if k=='noise_or_not':
    #         continue
    #     whole_data[k].extend(data_41[k])


    # only pos
    whole_data = copy.deepcopy(data_1)
    # only pos
    # whole_data = copy.deepcopy(data_41)
    # del data_41['noise_or_not']
    # clean
    clean_texts = []
    for idx,text in enumerate(whole_data['text']):
        text = text.split()
        temp_text = []
        for word in text:
            word:str
            if not word.startswith(("</O:","<O:","</S:","<S:")):
                temp_text.append(word)
        clean_texts.append(' '.join(temp_text))
    whole_data['text'] = clean_texts
    whole_data['p_label'] = copy.deepcopy(whole_data['top1'])
    print(sum(np.array(whole_data['p_label'])==np.array(whole_data['label']))/len(whole_data['p_label']))
    # save(whole_data,"/home/tywang/myURE/URE/fine_tune/data/tac_num681_only_neg_acc0.98_for_finetune_mnli_raw.pkl")
    #return whole_data


if __name__=="__main__":
    parser = argparse.ArgumentParser()
    # parser.add_argument("--data_path", type=str,default="/home/tywang/myURE/URE/fine_tune/data/tac_num681_only_neg_acc0.98_for_finetune_mnli_raw.pkl", help="as named")
    parser.add_argument("--seed", type=int,default=16, help="as named")
    parser.add_argument("--ratio", type=float, help="as named")
    
    
    """tac"""
    # parser.add_argument("--label2id_path", type=str,default="/home/tywang/myURE/URE/O2U_bert/tac_data/whole/rel2id.pkl", help="as named")
    # parser.add_argument("--selected_data_path", type=str,default="/home/tywang/myURE/URE/fine_tune/data/top_3406_41rels_acc0.7686.pkl", help="as named")
    # parser.add_argument("--config_path", type=str,default="/home/tywang/myURE/URE_mnli/relation_classification/configs/config_tac_partial_constrain.json", help="as named")
    # parser.add_argument("--template2label_path", type=str,default="/home/tywang/myURE/URE/O2U_bert/tac_data/whole/train_template2label.pkl", help="as named")
    
    """wiki"""

    parser.add_argument("--label2id_path", type=str,default="/home/tywang/myURE/URE/WIKI/typed/label2id.pkl", help="as named")
    parser.add_argument("--selected_data_path", type=str,default='/home/tywang/myURE/URE/fine_tune/data/wiki_NLNL_top4032_rels43_acc0.8227.pkl', help="as named")
    parser.add_argument("--config_path", type=str,default="/home/tywang/myURE/URE_mnli/relation_classification/configs/config_wiki_partial_constraint.json", help="as named")
    parser.add_argument("--template2label_path", type=str,default="/home/tywang/myURE/URE/WIKI/typed/train_template2label.pkl", help="as named")
    parser.add_argument("--random", type=bool,default=True, help="as named")
    
    args = parser.parse_args()
    set_global_random_seed(args.seed)
    # tacred2mnli_main(args)
    from_selected(args)