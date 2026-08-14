"""语音识别 Provider — 可配置切换 DashScope Paraformer / OpenAI 兼容 Whisper API。

接口统一为 transcribe(audio_bytes, filename) -> str，
voice.py 只依赖接口，不关心底层是 Paraformer 还是 Whisper。
"""
import os
import tempfile
from abc import ABC, abstractmethod

from app.core.config import settings
from app.core.logger import log


class BaseASRProvider(ABC):
    """语音识别抽象接口。"""

    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, filename: str) -> str:
        """转写音频为文字。"""


class DashScopeASRProvider(BaseASRProvider):
    """DashScope Paraformer 录音文件识别（阿里云，中文效果好）。"""

    async def transcribe(self, audio_bytes: bytes, filename: str) -> str:
        import dashscope
        dashscope.api_key = settings.DASHSCOPE_API_KEY

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

            # 下载转写结果 JSON，提取文字（transcripts[].text）
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


class WhisperAPIProvider(BaseASRProvider):
    """OpenAI 兼容 Whisper API。

    适配：OpenAI 官方 whisper-1，或任意 OpenAI 兼容的自托管服务
    （如 faster-whisper-server、whisper.cpp server 的 /v1/audio/transcriptions）。
    """

    async def transcribe(self, audio_bytes: bytes, filename: str) -> str:
        import httpx
        from app.agents.settings.model import MODEL_CONFIG
        base = (MODEL_CONFIG.get("asr_whisper_base") or settings.ASR_WHISPER_API_BASE).rstrip("/")
        key = MODEL_CONFIG.get("asr_whisper_key") or settings.ASR_WHISPER_API_KEY
        model = MODEL_CONFIG.get("asr_whisper_model") or settings.ASR_WHISPER_MODEL
        url = f"{base}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {key}"}
        files = {"file": (filename, audio_bytes, "application/octet-stream")}
        data = {"model": model}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, headers=headers, files=files, data=data)
        if resp.status_code != 200:
            raise RuntimeError(f"Whisper API 失败: {resp.status_code} {resp.text[:200]}")
        payload = resp.json()
        return str(payload.get("text", ""))


def get_asr_provider() -> BaseASRProvider:
    """按配置返回语音识别 Provider（优先 DB，回退 .env）。"""
    from app.agents.settings.model import MODEL_CONFIG
    provider = MODEL_CONFIG.get("asr_provider") or settings.ASR_PROVIDER
    if provider == "whisper":
        base = MODEL_CONFIG.get("asr_whisper_base") or settings.ASR_WHISPER_API_BASE
        if not base:
            log.warning("[ASR] ASR_PROVIDER=whisper 但未配置 Whisper 端点，回退 DashScope")
            return DashScopeASRProvider()
        return WhisperAPIProvider()
    return DashScopeASRProvider()
