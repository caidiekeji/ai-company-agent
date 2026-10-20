"""内置厂商注册表（PRD F-MOD-05：清单可扩展，新增厂商不改核心代码）。

api_format:
  - openai      : OpenAI 兼容（DeepSeek/GLM/Moonshot/硅基流动/Ollama/OpenAI/自定义）
  - anthropic   : Anthropic Messages API
  - gemini      : Google Gemini
  - azure_tts   : 预留
本地引擎（engine_type=local）无凭证字段。
"""

VENDORS: dict[str, list[dict]] = {
    "llm": [
        {"id": "deepseek", "name": "DeepSeek", "api_format": "openai", "base_url": "https://api.deepseek.com/v1"},
        {"id": "zhipu", "name": "智谱 GLM", "api_format": "openai", "base_url": "https://open.bigmodel.cn/api/paas/v4"},
        {"id": "moonshot", "name": "Moonshot", "api_format": "openai", "base_url": "https://api.moonshot.cn/v1"},
        {"id": "siliconflow", "name": "硅基流动", "api_format": "openai", "base_url": "https://api.siliconflow.cn/v1"},
        {"id": "openai", "name": "OpenAI", "api_format": "openai", "base_url": "https://api.openai.com/v1"},
        {"id": "anthropic", "name": "Anthropic", "api_format": "anthropic", "base_url": "https://api.anthropic.com"},
        {"id": "google", "name": "Google Gemini", "api_format": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta"},
        {"id": "ollama", "name": "Ollama（本地）", "api_format": "openai", "base_url": "http://127.0.0.1:11434/v1", "local": True},
        {"id": "openai_compat", "name": "自定义（OpenAI 兼容）", "api_format": "openai", "base_url": ""},
        {"id": "mock", "name": "Mock（离线演示/测试）", "api_format": "mock", "base_url": "", "local": True},
    ],
    "asr": [
        {"id": "openai_asr", "name": "OpenAI Whisper API", "api_format": "openai_asr", "base_url": "https://api.openai.com/v1"},
        {"id": "aliyun_asr", "name": "阿里云智能语音", "api_format": "cloud_asr", "base_url": ""},
        {"id": "tencent_asr", "name": "腾讯云 ASR", "api_format": "cloud_asr", "base_url": ""},
        {"id": "xfyun_asr", "name": "讯飞 ASR", "api_format": "cloud_asr", "base_url": ""},
        {"id": "sherpa_asr", "name": "sherpa-onnx（本地）", "api_format": "local", "base_url": "", "local": True},
        {"id": "whisper_cpp", "name": "whisper.cpp（本地）", "api_format": "local", "base_url": "", "local": True},
    ],
    "tts": [
        {"id": "openai_tts", "name": "OpenAI TTS", "api_format": "openai_tts", "base_url": "https://api.openai.com/v1"},
        {"id": "azure_tts", "name": "Azure TTS", "api_format": "azure_tts", "base_url": ""},
        {"id": "aliyun_tts", "name": "阿里云 CosyVoice", "api_format": "cloud_tts", "base_url": ""},
        {"id": "tencent_tts", "name": "腾讯云 TTS", "api_format": "cloud_tts", "base_url": ""},
        {"id": "volcengine_tts", "name": "火山引擎 TTS", "api_format": "cloud_tts", "base_url": ""},
        {"id": "moss_tts", "name": "MOSS-TTS（本地）", "api_format": "local", "base_url": "", "local": True},
        {"id": "cosyvoice_local", "name": "CosyVoice（本地）", "api_format": "local", "base_url": "", "local": True},
    ],
}

# 无标准模型列表接口的厂商 → 内置清单兜底（PRD 约束）
BUILTIN_MODELS: dict[str, list[str]] = {
    "aliyun_asr": ["paraformer-v2", "paraformer-general-v2"],
    "tencent_asr": ["16k_zh", "16k_zh-cn"],
    "xfyun_asr": ["iat"],
    "aliyun_tts": ["cosyvoice-v1", "cosyvoice-v2"],
    "tencent_tts": ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"],
    "volcengine_tts": ["zh_female_cancan", "zh_male_yuanboxiaoao"],
    "sherpa_asr": ["sherpa-zh-en-faster-whisper-small", "sherpa-zh-conformer"],
    "whisper_cpp": ["whisper-small", "whisper-base"],
    "moss_tts": ["moss-tts-nano"],
    "cosyvoice_local": ["cosyvoice-mini"],
    "openai_asr": ["whisper-1", "gpt-4o-transcribe"],
    "openai_tts": ["tts-1", "tts-1-hd", "gpt-4o-mini-tts"],
}

# TTS 音色：厂商有独立音色接口的从接口拉，否则内置清单（附录 A 核实后生效）
BUILTIN_VOICES: dict[str, list[dict]] = {
    "openai_tts": [
        {"id": "alloy", "name": "Alloy（中性）"}, {"id": "echo", "name": "Echo（男）"},
        {"id": "fable", "name": "Fable（男）"}, {"id": "onyx", "name": "Onyx（男）"},
        {"id": "nova", "name": "Nova（女）"}, {"id": "shimmer", "name": "Shimmer（女）"},
    ],
    "azure_tts": [
        {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓（女）"}, {"id": "zh-CN-YunxiNeural", "name": "云希（男）"},
        {"id": "zh-CN-YunyangNeural", "name": "云扬（男）"}, {"id": "zh-CN-XiaoyiNeural", "name": "晓伊（女）"},
    ],
    "aliyun_tts": [
        {"id": "longxiaochun", "name": "龙小淳（女）"}, {"id": "longxiaobai", "name": "龙小白（女）"},
        {"id": "longcheng", "name": "龙橙（男）"},
    ],
}

# 性别 → 默认音色初始映射（附录 A 核实后生效；F-VOI-04：仅未手动指定时生效）
GENDER_VOICE_MAP = {"男": "male", "女": "female"}


def vendor_list(kind: str) -> list[dict]:
    return VENDORS.get(kind, [])


def vendor_of(kind: str, vendor_id: str) -> dict | None:
    return next((v for v in VENDORS.get(kind, []) if v["id"] == vendor_id), None)
