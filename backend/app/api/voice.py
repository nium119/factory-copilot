"""语音识别 API — 录音文件上传 + DashScope Paraformer 转写为文字。"""
import os
import tempfile

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.config import settings
from app.core.logger import log

router = APIRouter(prefix="/voice", tags=["语音"])


@router.post("/transcribe", summary="语音转文字")
async def transcribe_audio(file: UploadFile = File(...)):
    """接收录音文件，调 DashScope Paraformer 转写为文字。"""
    if not settings.DASHSCOPE_API_KEY:
        raise HTTPException(status_code=500, detail="未配置 DASHSCOPE_API_KEY")

    suffix = os.path.splitext(file.filename or "")[1] or ".wav"
    tmp_path = ""
    try:
        # 1. 落盘临时文件（DashScope 上传接口需要本地路径）
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        import dashscope
        dashscope.api_key = settings.DASHSCOPE_API_KEY

        # 2. 上传到 DashScope 临时存储，拿到 file_id
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

        # 3. 用 file_id 换可访问的临时 URL
        get_resp = Files.get(file_id)
        file_url = None
        gout = get_resp.output
        if isinstance(gout, dict):
            file_url = gout.get("url")
        if not file_url:
            raise RuntimeError("未获取到音频文件临时 URL")

        # 4. 语音识别（异步任务，返回转写结果 URL）
        from dashscope.audio.asr import Transcription
        result = Transcription.call(model="paraformer-v1", file_urls=[file_url])
        if result.status_code != 200:
            raise RuntimeError(f"语音识别失败: {result.code} {result.message}")

        # 5. 下载转写结果 JSON，提取文字（transcripts[].text）
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

        log.info(f"[Voice] 识别完成，字数: {len(text)}")
        return {"success": True, "text": text}
    except Exception as e:
        log.error(f"[Voice] 语音识别失败: {e}")
        raise HTTPException(status_code=500, detail=f"语音识别失败: {e}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
