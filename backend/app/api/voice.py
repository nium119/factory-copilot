"""语音识别 API — 录音文件上传 + 转写为文字（走配置的 ASR 模型）。"""
from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.logger import log
from app.services.asr_provider import create_asr

router = APIRouter(prefix="/voice", tags=["语音"])


@router.post("/transcribe", summary="语音转文字")
async def transcribe_audio(file: UploadFile = File(...)):
    """接收录音文件，转写为文字（按「模型配置」里选中的 type=asr 模型）。"""
    try:
        audio_bytes = await file.read()
        transcribe = create_asr()
        text = await transcribe(audio_bytes, file.filename or "recording.wav")
        log.info(f"[Voice] 识别完成，字数: {len(text)}")
        return {"success": True, "text": text}
    except Exception as e:
        log.error(f"[Voice] 语音识别失败: {e}")
        raise HTTPException(status_code=500, detail=f"语音识别失败: {e}")
