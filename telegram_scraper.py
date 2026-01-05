from telethon import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest
from datetime import datetime, timedelta
import asyncio
import os

class TelegramScraper:
    def __init__(self):
        self.api_id = os.getenv('TELEGRAM_API_ID')
        self.api_hash = os.getenv('TELEGRAM_API_HASH')
        self.phone = os.getenv('TELEGRAM_PHONE')  # Your phone number
        self.session_name = 'asifah_session'
        
    async def scrape_channels(self, channels, hours_back=24):
        """
        Scrape messages from specified Telegram channels
        
        channels: list of channel usernames (e.g., ['channel1', 'channel2'])
        hours_back: how many hours back to scrape
        """
        results = []
        
        async with TelegramClient(self.session_name, self.api_id, self.api_hash) as client:
            # You'll need to authenticate once - this will send a code to your Telegram
            if not await client.is_user_authorized():
                await client.send_code_request(self.phone)
                # Handle verification interactively first time
            
            since = datetime.now() - timedelta(hours=hours_back)
            
            for channel in channels:
                try:
                    entity = await client.get_entity(channel)
                    messages = await client(GetHistoryRequest(
                        peer=entity,
                        limit=100,
                        offset_date=since,
                        offset_id=0,
                        max_id=0,
                        min_id=0,
                        add_offset=0,
                        hash=0
                    ))
                    
                    for message in messages.messages:
                        if message.date > since and message.message:
                            results.append({
                                'channel': channel,
                                'date': message.date,
                                'text': message.message,
                                'views': getattr(message, 'views', 0),
                                'forwards': getattr(message, 'forwards', 0)
                            })
                            
                except Exception as e:
                    print(f"Error scraping {channel}: {e}")
                    
        return results
    
    def analyze_for_threats(self, messages, target='hezbollah'):
        """Analyze messages for threat indicators"""
        escalation_keywords = {
            'hezbollah': ['حزب الله', 'Hezbollah', 'نصرالله', 'Nasrallah', 'southern lebanon', 'لبنان الجنوبي'],
            'iran': ['ایران', 'Iran', 'Tehran', 'تهران', 'IRGC', 'سپاه'],
            'houthis': ['الحوثي', 'Houthis', 'Yemen', 'اليمن', 'Ansar Allah']
        }
        
        action_keywords = ['strike', 'attack', 'operation', 'target', 'raid', 
                          'هجوم', 'ضربة', 'عملية', 'מתקפה', 'תקיפה']
        
        relevant_messages = []
        threat_score = 0
        
        for msg in messages:
            text_lower = msg['text'].lower()
            
            # Check if message is relevant
            if any(kw.lower() in text_lower for kw in escalation_keywords.get(target, [])):
                relevant_messages.append(msg)
                
                # Increase score for action keywords
                if any(kw in text_lower for kw in action_keywords):
                    threat_score += 2
                    
                # Weight by engagement
                if msg.get('views', 0) > 10000:
                    threat_score += 1
                    
        return {
            'relevant_count': len(relevant_messages),
            'threat_score': threat_score,
            'top_messages': relevant_messages[:5]
        }
