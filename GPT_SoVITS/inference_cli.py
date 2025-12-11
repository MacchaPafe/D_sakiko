import os
import time

if os.path.exists('../reference_audio/GSV_sample_rate.txt'):
    with (open('../reference_audio/GSV_sample_rate.txt', 'r', encoding='utf-8') as f):
        gsv_sam_rate = int(f.read())
else:
    # 在指定位置创建文件并写入初始值
    with open('../reference_audio/GSV_sample_rate.txt', 'w', encoding='utf-8') as f:
        f.write('16')
    gsv_sam_rate = 16

def synthesize(to_gptsovits_queue,from_gptsovits_queue,from_gptsovits_queue2):
    from inference_webui import change_gpt_weights, change_sovits_weights, get_tts_wav
    import soundfile as sf
    from tools.i18n.i18n import I18nAuto
    i18n = I18nAuto()
    while True:
        while True:
            if not to_gptsovits_queue.empty():

                info=to_gptsovits_queue.get()
                if info[0]==0:
                    from_gptsovits_queue.put('wait')
                    change_gpt_weights(gpt_path=info[1])
                    change_sovits_weights(sovits_path=info[2])
                    from_gptsovits_queue.put('done')
                    continue
                else:
                    break
            time.sleep(0.2)

        if info=='bye':
            # print("推理模块收到退出信号，正在退出...")
            break

        ref_text_path=info[2]
        with open(ref_text_path, 'r', encoding='utf-8') as file:
            ref_text = file.read()

        '''# Read target text
        with open(target_text_path, 'r', encoding='utf-8') as file:
            target_text = file.read()'''


        try:
        # Synthesize audio
            synthesis_result = get_tts_wav(ref_wav_path=info[1],
                                       prompt_text=ref_text,
                                       prompt_language=i18n(info[3]),
                                       text=info[4],
                                       text_language=i18n(info[5]),
                                       speed=info[7],how_to_cut=info[8],top_p=1, temperature=1
                                       ,sample_steps=gsv_sam_rate
                                       ,pause_second=info[9],
                                        message_queue=from_gptsovits_queue2
                                       )
            result_list = list(synthesis_result)
            if result_list:
                last_sampling_rate, last_audio_data = result_list[-1]
                with open('../reference_audio/audio_generate_count.txt','r',encoding='utf-8') as f:
                    generate_count=int(f.read())
                output_wav_path = os.path.join(info[6], f"output{generate_count}.wav")
                generate_count+=1
                with open('../reference_audio/audio_generate_count.txt','w',encoding='utf-8') as f:
                    f.write(str(generate_count))
                sf.write(output_wav_path, last_audio_data, last_sampling_rate)
                from_gptsovits_queue.put(output_wav_path)
        except Exception as e:
            print( '语音合成错误信息：',e)
            generate_count += 1
            from_gptsovits_queue.put('../reference_audio\\silent_audio\\silence.wav')
    # print("推理模块已退出完成")




'''def main():
    parser = argparse.ArgumentParser(description="GPT-SoVITS Command Line Tool")
    parser.add_argument('--gpt_model', required=True, help="Path to the GPT model file")
    parser.add_argument('--sovits_model', required=True, help="Path to the SoVITS model file")
    parser.add_argument('--ref_audio', required=True, help="Path to the reference audio file")
    parser.add_argument('--ref_text', required=True, help="Path to the reference text file")
    parser.add_argument('--ref_language', required=True, choices=["中文", "英文", "日文"], help="Language of the reference audio")
    parser.add_argument('--target_text', required=True, help="Path to the target text file")
    parser.add_argument('--target_language', required=True, choices=["中文", "英文", "日文", "中英混合", "日英混合", "多语种混合"], help="Language of the target text")
    parser.add_argument('--output_path', required=True, help="Path to the output directory")

    args = parser.parse_args()

    synthesize(args.gpt_model, args.sovits_model, args.ref_audio, args.ref_text, args.ref_language, args.target_text, args.target_language, args.output_path,speed=1,how_to_cut="不切")

if __name__ == '__main__':
    main()'''

