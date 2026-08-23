"""
Sentinel Factory - RAG Agent API Server
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import json
import glob
import os
from datetime import datetime
from langchain_ollama import OllamaLLM
import uvicorn

app = FastAPI(title="Sentinel Factory RAG Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class RAGAgent:
    def __init__(self):
        self.all_data = self._load_json_data()
        self.context = json.dumps(self.all_data, indent=2)[:5000]
        self.llm = OllamaLLM(model="mistral", temperature=0.1)
        print(f"✅ RAG Agent initialized with {len(self.all_data)} records")
    
    def _load_json_data(self):
        all_data = []
        json_files = glob.glob("endpoints/*.json")
        
        if not json_files:
            print("⚠️ No JSON files found")
            return []
        
        for file in json_files:
            with open(file, 'r') as f:
                data = json.load(f)
                if isinstance(data, list):
                    all_data.extend(data)
                elif isinstance(data, dict):
                    all_data.append(data)
            print(f"📂 Loaded {os.path.basename(file)}: {len(data)} records")
        
        return all_data
    
    def ask(self, question: str) -> str:
        if not self.all_data:
            return "No data available."
        
        prompt = f"""
        You are a safety monitoring assistant for Sentinel Factory.
        
        Monitoring Data:
        {self.context}
        
        User Question: {question}
        
        Answer based ONLY on the data provided. Be specific with details.
        """
        
        try:
            response = self.llm.invoke(prompt)
            return response
        except Exception as e:
            return f"Error: {str(e)}"

rag_agent = RAGAgent()

class QuestionRequest(BaseModel):
    question: str

@app.get("/")
async def root():
    return {"message": "RAG Agent API", "docs": "/docs"}

@app.post("/ask")
async def ask_question(request: QuestionRequest):
    try:
        answer = rag_agent.ask(request.question)
        return {"answer": answer, "status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status")
async def status():
    return {
        "status": "active",
        "model": "mistral",
        "records": len(rag_agent.all_data)
    }

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8001, reload=False)