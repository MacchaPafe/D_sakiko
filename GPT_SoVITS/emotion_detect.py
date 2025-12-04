from transformers import AutoTokenizer,AutoModelForSequenceClassification,Trainer,TrainingArguments
from datasets import load_dataset
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.metrics import f1_score


#读取文件
df = pd.read_csv('cleaned_emotion_data.csv')

#定义转换字典
target_map={
    'happiness':0,
    'sadness':1,
    'anger':2,
    'disgust':3,
    'like':4,
    'surprise':5,
    'fear':6
}

#将文件中的对应单词转换为数字 单独列出一列
df['label'] = df['label'].map(target_map)



raw_datasets=load_dataset('csv',data_files='data.csv')    #加载训练的数据格式

split =raw_datasets['train'].train_test_split(test_size=0.2, seed=42)     #分隔为数据集和测试集 测试集占百分之30



tokenizer = AutoTokenizer.from_pretrained(
    'D:\\python_project\\sovits_api_deepseek\\bert-base-chinese')

def tokenize_fn(batch):
    return tokenizer(batch['text'],truncation=True)            #将数据集中的句子使用标记器进行标记化处理，以便后续在自然语言处理任务中使用

tokenized_datasets = split.map(tokenize_fn,batched=True)




model = AutoModelForSequenceClassification.from_pretrained('D:\\python_project\\sovits_api_deepseek\\bert-base-chinese',
                                                           num_labels=7)

params_before = []  # 遍历模型中的所有参数，并将其初始值添加到params_before列表中，以便后续比较模型参数的变化。
for name, p in model.named_parameters():
    params_before.append(p.detach().cpu().numpy())


training_args = TrainingArguments(
    output_dir='training_dir',     #输出文件夹
    evaluation_strategy='epoch',
    save_strategy='epoch',
    num_train_epochs=5,            #训练轮次
    per_device_train_batch_size=16,
    per_device_eval_batch_size=64
)

def compute_metrics(logits_and_labels):
    logits, labels = logits_and_labels
    predictions = np.argmax(logits, axis=-1)
    acc = np.mean(predictions == labels)
    f1 = f1_score(labels, predictions, average='macro')
    return {'===accuracy===': acc, '===f1===': f1}


trainer = Trainer(
    model,  # 模型实例，用于训练
    training_args,  # 训练参数，包括学习率、批大小、训练轮数等
    train_dataset=tokenized_datasets["train"],  # 训练数据集
    eval_dataset=tokenized_datasets["test"],  # 验证数据集
    tokenizer=tokenizer,  # 分词器，用于对输入进行分词
    compute_metrics=compute_metrics  # 用于计算性能指标的函数
)


trainer.train()


