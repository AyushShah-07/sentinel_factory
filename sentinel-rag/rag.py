"""
RAG Agent Module - Import this in your FastAPI server
"""
import json
import glob
from langchain_ollama import OllamaLLM

class RAGAgent:
    def __init__(self):
        # Load all JSON data once
        self.all_data = []
        for file in glob.glob("endpoints/*.json"):
            with open(file, 'r') as f:
                data = json.load(f)
                self.all_data.extend(data)
        
        self.context = json.dumps(self.all_data, indent=2)
        self.llm = OllamaLLM(model="mistral", temperature=0.1)  # ✅ FIXED
    
    def ask(self, question: str) -> str:
        """Ask question to RAG agent"""
        prompt = f"""
        You are a safety monitoring assistant for Sentinel Factory.
        
        Data:
        {self.context}
        
        Question: {question}
        
        Answer based on the data provided.
        """
        
        response = self.llm.invoke(prompt)
        return response  # OllamaLLM returns string directly, not .content

# Create singleton instance
rag_agent = RAGAgent()