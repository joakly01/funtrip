"""安装官方 Whisper base 离线模型，音频转写不需要额外 API 密钥。"""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
from cloud_trading.config import Config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, help='指定模型保存目录；默认使用应用配置')
    args = parser.parse_args()
    for key, value in dotenv_values(ROOT / 'config/app.env', interpolate=False).items():
        if key.startswith('TRADING_') and value is not None:
            os.environ.setdefault(key, value)
    config = Config.from_env()
    destination = args.directory or config.asr_model_dir or config.runtime_dir / 'models/faster-whisper-base'
    from huggingface_hub import snapshot_download
    snapshot_download('Systran/faster-whisper-base', local_dir=destination,
        allow_patterns=['model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt'])
    print('语音识别模型已安装，重新打开网页后即可上传录音。')


if __name__ == '__main__':
    main()
