

import os,sys
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

import inference_cli
GPT_model="../GPT_weights_v4/sakiko-e15.ckpt"
SoVITS_model='../SoVITS_weights_v4/sakiko_e4_s520_l64.pth'
ref_audio_path='../reference_audio/black_sakiko.wav'
ref_text_path='../reference_audio/reference_text_black_sakiko.txt'
ref_language="日文"
target_text="我喜欢吃东西，以及音乐。我等会要去睡觉，在学校的长椅上，也可能是树上。起来之后，要去吃抹茶芭菲。再然后，去排练室排练。"
target_language="中文"
output_path="../reference_audio/generated_audios_temp"
speed=0.87
a=["我喜欢吃东西，以及音乐。", '我等会要去睡觉，在学校的长椅上，也可能是树上。','起来之后，要去吃抹茶芭菲。再然后，去排练室排练。']
for _,data in enumerate(a):
    inference_cli.synthesize(GPT_model_path=GPT_model,
                         SoVITS_model_path=SoVITS_model,
                         ref_audio_path=ref_audio_path,
                         ref_text_path=ref_text_path,
                         ref_language=ref_language,
                         target_text=data,
                         target_language=target_language,
                         output_path=output_path,
                         speed=speed,
                         how_to_cut="不切"
                         )



