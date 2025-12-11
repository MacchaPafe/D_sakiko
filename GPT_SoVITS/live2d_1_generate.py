from pathlib import Path
from typing import Union
import os


def generate_function_with_api_key(api_key: str, output_file_name: Union[str, Path] = "live2d_1.pyx"):
    """
    生成一个“返回 live2d 的函数”的代码片段，并将其写入指定的输出文件中。
    
    :param api_key: 输入 live2d 字符串
    :param output_file_name: 输出文件的名称，默认为 "live2d_1.pyx"
    """
    function_code = f"""def get_live2d():
    return "{api_key}"
    """

    with open(output_file_name, "w", encoding="utf-8") as file:
        file.write(function_code)


if __name__ == "__main__":
    api_key_input = os.environ.get("LIVE2D_KEY")
    if not api_key_input:
        raise ValueError("请设置环境变量 LIVE2D_KEY")

    generate_function_with_api_key(api_key_input)