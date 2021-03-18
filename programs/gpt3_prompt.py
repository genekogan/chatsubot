from easydict import EasyDict
import asyncio
import re
import gpt3


async def run(settings, 
              message):
    
    response = gpt3.complete(
        settings.prompt, 
        stops=settings.stops if 'stops' in settings else None, 
        max_tokens=settings.max_tokens if 'max_tokens' in settings else 50, 
        temperature=settings.temperature if 'temperature' in settings else 0.9, 
        engine=settings.engine if 'engine' in settings else 'davinci',
        max_completions=3)
    
    if 'remove_empty_lines' in settings:
        response = re.sub(r'[\n]+', '\\n', response)

    if 'preface' in settings:
        response = settings.preface + response
        
    return response

