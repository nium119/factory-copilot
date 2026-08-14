"""语音识别 Provider — 按模型配置（type=asr）选择 DashScope Paraformer / OpenAI 兼容 Whisper。

与 LLM / Embedding 一致：模型统一在「模型配置」里管理，type=asr，
create_asr() 按 selection.asr_model 选中模型 + provider 注册表分发。
"""
import os
import tempfile

from app.core.config import settings


async def _transcribe_dashscope(audio_bytes: bytes, filename: str, cfg: dict) -> str:
    """DashScope Paraformer 录音文件识别。"""
    import dashscope
    dashscope.api_key = cfg.get("api_key") or settings.DASHSCOPE_API_KEY

    suffix = os.path.splitext(filename)[1] or ".wav"
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        from dashscope import Files
        upload_resp = Files.upload(tmp_path, purpose="inference")
        if upload_resp.status_code != 200:
            raise RuntimeError(f"音频上传失败: {upload_resp.code} {upload_resp.message}")
        file_id = None
        out = upload_resp.output
        if isinstance(out, dict):
            uploaded = out.get("uploaded_files") or []
            if uploaded and isinstance(uploaded[0], dict):
                file_id = uploaded[0].get("file_id")
        if not file_id:
            raise RuntimeError("音频上传成功但未返回 file_id")

        file_url = None
        gout = Files.get(file_id).output
        if isinstance(gout, dict):
            file_url = gout.get("url")
        if not file_url:
            raise RuntimeError("未获取到音频文件临时 URL")

        from dashscope.audio.asr import Transcription
        result = Transcription.call(model="paraformer-v1", file_urls=[file_url])
        if result.status_code != 200:
            raise RuntimeError(f"语音识别失败: {result.code} {result.message}")

        import httpx
        text = ""
        out = result.output
        results = out.get("results") if isinstance(out, dict) else None
        if results and isinstance(results[0], dict):
            tu = results[0].get("transcription_url")
            if tu:
                trans_json = httpx.get(tu, timeout=30).json()
                transcripts = trans_json.get("transcripts") or []
                text = "".join(
                    str(t.get("text", "")) for t in transcripts if isinstance(t, dict)
                )
        return text
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


async def _transcribe_whisper(audio_bytes: bytes, filename: str, cfg: dict) -> str:
    """OpenAI 兼容 Whisper API（whisper-1 / 自托管 faster-whisper-server 等）。"""
    import httpx
    base = (cfg.get("api_url") or "").rstrip("/")
    if not base:
        raise RuntimeError("Whisper 模型未配置 API 地址")
    key = cfg.get("api_key") or ""
    model = cfg.get("name") or "whisper-1"
    url = f"{base}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {key}"}
    files = {"file": (filename, audio_bytes, "application/octet-stream")}
    data = {"model": model}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, headers=headers, files=files, data=data)
    if resp.status_code != 200:
        raise RuntimeError(f"Whisper API 失败: {resp.status_code} {resp.text[:200]}")
    return str(resp.json().get("text", ""))


# provider → transcribe 工厂
_ASR_REGISTRY = {
    "qwen": _transcribe_dashscope,
    "dashscope": _transcribe_dashscope,
    "openai": _transcribe_whisper,
    "ollama": _transcribe_whisper,
    "custom": _transcribe_whisper,
}


def create_asr():
    """按 selection.asr_model（type=asr）返回 transcribe 函数，回退 DashScope。"""
    from app.agents.settings.model import MODEL_CONFIG
    from app.core.model_config import _load_all_models

    models = _load_all_models()
    preferred = MODEL_CONFIG.get("asr_model") or "paraformer-v1"
    m = models.get(preferred, {})
    provider = m.get("provider", "")
    fn = _ASR_REGISTRY.get(provider)
    # DashScope 可复用 .env 的 DASHSCOPE_API_KEY，允许 api_key 为空
    if not (m.get("enabled") and fn and (m.get("api_key") or provider == "qwen")):
        m = {"api_key": settings.DASHSCOPE_API_KEY, "name": "paraformer-v1", "provider": "qwen"}
        fn = _transcribe_dashscope

    async def transcribe(audio_bytes: bytes, filename: str) -> str:
        return await fn(audio_bytes, filename, m)

    return transcribe
