import inference_cli


GPT_model="../GPT_weights_v2/rana-e20.ckpt"
SoVITS_model='../SoVITS_weights_v2/rana_e12_s264.pth'
ref_audio_path='reference_audio/rana_vocal_final.wav_0001564160_0001697920.wav'
ref_text_path='reference_audio/reference_text.txt'
ref_language="日文"
target_text="我喜欢吃东西，以及音乐。我等会要去睡觉，在学校的长椅上，也可能是树上。起来之后，要去吃抹茶芭菲。再然后，去排练室排练。"
target_language="中文"
output_path="reference_audio/generated_audios_temp"
speed=0.87
inference_cli.synthesize(GPT_model_path=GPT_model,
                         SoVITS_model_path=SoVITS_model,
                         ref_audio_path=ref_audio_path,
                         ref_text_path=ref_text_path,
                         ref_language=ref_language,
                         target_text=target_text,
                         target_language=target_language,
                         output_path=output_path,
                         speed=speed,
                         how_to_cut="不切"
                         )



