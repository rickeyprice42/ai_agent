"""Live synthetic vision check; sends only a generated QA image."""
from pathlib import Path
from io import BytesIO
from time import perf_counter
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ai_agent.config import Settings
from ai_agent.llm.vision import OpenAIVisionProvider,OllamaVisionProvider
from PIL import Image,ImageDraw,ImageFont

def main():
    sys.stdout.reconfigure(encoding='utf8')
    settings=Settings.load(ROOT)
    image=Image.new('RGB',(640,320),'white')
    draw=ImageDraw.Draw(image)
    draw.rectangle((30,40,170,180),fill='blue')
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',32)
    draw.text((200,80),'AVELIN QA 742',fill='black',font=font)
    data=BytesIO();image.save(data,format='PNG')
    rows=[]
    providers=[OpenAIVisionProvider(model='gpt-6.1-sol',api_key=settings.openai_api_key,base_url=settings.openai_base_url,timeout_seconds=65),OllamaVisionProvider(model=settings.vision_primary_model,api_key=settings.ollama_api_key,base_url=settings.ollama_cloud_url,timeout_seconds=65)]
    for provider in providers:
        started=perf_counter()
        try:
            text=provider.analyze(data.getvalue(),'image/png','Read the exact text and name the shape and color. Answer in English briefly.')
            status='PASS' if '742' in text and 'blue' in text.lower() and 'square' in text.lower() else 'FAIL'
        except Exception as exc: text=f'{type(exc).__name__}: {exc}';status='FAIL'
        rows.append({'provider':provider.name,'model':provider.model,'status':status,'response':text,'duration_s':round(perf_counter()-started,3)})
        print(json.dumps(rows[-1],ensure_ascii=False),flush=True)
    (ROOT/'.local/audit/vision-results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')

if __name__=='__main__':main()
