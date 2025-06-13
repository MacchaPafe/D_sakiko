import time


class EmotionDetect:
    def __init__(self,model_path="./training_dir/checkpoint-8830"):
        self.MODEL_PATH=model_path

    def launch_emotion_detect(self):
        #print("启动语义检测模型")
        from transformers import pipeline
        model=pipeline('text-classification',model=self.MODEL_PATH,device=-1)
        return model


if __name__=="__main__":
    a=EmotionDetect()
    model=a.launch_emotion_detect()
    while True:

        user_input=input('bye退出程序')

        if user_input=='bye':
            break
        t0 = time.time()
        print(model(user_input)[0]['label'])
        print(time.time()-t0)
