from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import uvicorn

app = FastAPI(title="Agent Backend API", version="1.0.0")

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000","http://41.103.96.248:8080"],  # React开发服务器地址
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 定义请求模型
class Message(BaseModel):
    content: str
    session_id: Optional[str] = None

class AgentResponse(BaseModel):
    response: str
    session_id: str
    status: str

@app.get("/")
async def root():
    return {"message": "Agent Backend API is running"}

@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}

@app.post("/api/chat", response_model=AgentResponse)
async def chat(message: Message):
    """
    Agent聊天接口
    """
    # TODO: 实现Agent逻辑
    return AgentResponse(
        response=f"收到消息: {message.content}",
        session_id=message.session_id or "default",
        status="success"
    )

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
