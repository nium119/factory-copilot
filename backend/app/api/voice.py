"""语音识别 API — 文件转写 + 实时流式转写（走配置的 ASR 模型）。"""
import asyncio
import json

from fastapi import APIRouter, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect

from app.core.logger import log
from app.services.asr_provider import create_asr, create_stream_asr

router = APIRouter(prefix="/voice", tags=["语音"])


@router.post("/transcribe", summary="语音转文字（文件）")
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


@router.websocket("/stream", name="语音实时转写")
async def voice_stream(ws: WebSocket):
    """实时语音识别：前端推 PCM 音频帧 → 转发 DashScope Paraformer → 实时回传 partial/final。

    协议（前端 → 后端）：
      - 二进制帧：PCM 16bit 16kHz 单声道音频
      - 文本帧 {"type": "end"}：结束录音，触发最终识别

    协议（后端 → 前端，JSON 文本帧）：
      - {"type": "partial", "text": "..."}   中间结果（实时字幕）
      - {"type": "final",   "text": "..."}   定稿结果
      - {"type": "complete"}                 识别结束
      - {"type": "error", "message": "..."}  错误
    """
    await ws.accept()
    recognition = None
    try:
        cfg = create_stream_asr()
        import dashscope
        dashscope.api_key = cfg["api_key"]

        from dashscope.audio.asr import Recognition, RecognitionCallback

        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        state = {"finals": [], "partial": ""}

        class Callback(RecognitionCallback):
            def on_event(self, result):
                sentence = result.get_sentence()
                if isinstance(sentence, list):
                    sentence = sentence[-1] if sentence else None
                if not isinstance(sentence, dict):
                    return
                text = sentence.get("text", "")
                if not text:
                    return
                if result.is_sentence_end(sentence):
                    state["finals"].append(text)
                    state["partial"] = ""
                    full = "".join(state["finals"])
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "final", "text": full})
                else:
                    state["partial"] = text
                    full = "".join(state["finals"]) + state["partial"]
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "partial", "text": full})

            def on_error(self, result):
                msg = getattr(result, "message", "识别出错")
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "message": msg})

            def on_complete(self):
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "complete"})

        recognition = Recognition(
            model=cfg["model"],
            format=cfg["format"],
            sample_rate=cfg["sample_rate"],
            callback=Callback(),
        )
        recognition.start()

        # 结果转发协程：消费 callback 结果，转发给前端
        async def forward_results():
            while True:
                item = await queue.get()
                if item["type"] in ("partial", "final", "error"):
                    await ws.send_json(item)
                if item["type"] in ("complete", "error"):
                    break

        fwd = asyncio.create_task(forward_results())

        # 接收前端音频帧
        ended = False
        while not ended:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data is not None:
                recognition.send_audio_frame(data)
                continue
            # 文本控制消息
            try:
                ctrl = json.loads(msg.get("text") or "")
            except Exception:
                continue
            if ctrl.get("type") == "end":
                ended = True

        # 结束识别（阻塞等待 DashScope 返回最终结果 + on_complete）
        await asyncio.to_thread(recognition.stop)
        try:
            await asyncio.wait_for(fwd, timeout=5)
        except asyncio.TimeoutError:
            pass

        # 兜底：确保发一个 complete 给前端
        try:
            await ws.send_json({"type": "complete"})
        except Exception:
            pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error(f"[Voice] 流式识别失败: {e}")
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        if recognition is not None:
            try:
                await asyncio.to_thread(recognition.stop)
            except Exception:
                pass
