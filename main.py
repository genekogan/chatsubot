from dotenv import load_dotenv
from easydict import EasyDict
from datetime import datetime, timedelta, timezone
from suntime import Sun, SunTimeException
import sched
import asyncio
import logging
import itertools
import time
import math
import os
import re
import json
import random
import requests
import numpy as np

import discord
import spotipy
import gpt3

from programs import gpt3_chat
from programs import gpt3_prompt
from programs import calendar
from programs import spotify
from programs import instagram
from programs import ifttt
#from programs import ml4a_client

from bots import bots
from emojis import emoji_docs

# setup logging
# logging.basicConfig(
#     level=logging.DEBUG, 
#     format='%(asctime)s %(levelname)-8s %(message)s', 
#     datefmt='%a, %d %b %Y %H:%M:%S', 
#     filename='log_bots2.txt', 
#     filemode='w'
# )

# Reactions/emoji preferences
emoji_search_results = {}
reactions_enabled = False


# Which bots to run from the bots directory
botlist_2021 = [
    'sunrisesunset', 'mesa', 'mechanicalduck', 
    'chatsubo', 'wall-e', 'eve', 
    'facts', 'philosophy', 'deeplearning', 
    'kitchen', 'qa', 'coach', 
    'oracle', 'quest', 'astronauts', 'sentient_machine'
]

botlist_2022 = ['chiba', 'abraham']
botlist = botlist_2022



def utc_to_local(utc_dt):
    return utc_dt.replace(tzinfo=timezone.utc).astimezone(tz=None)


class DiscordBot(discord.Client):
       
    async def setup(self, settings):
        self.ready = False
        self.settings = EasyDict(settings)
        self.timestamps = []
        self.last_senders = {}
        self.last_timestamps = {}
        self.member2var = None
        self.var2member = None
        token = os.getenv(self.settings.token_env) 
        await self.start(token)
        
        
    async def on_ready(self):
        if self.ready:
            print('{} has re-connected.'.format(self.user))
            return

        self.ready = True
        guild_names = [guild.name for guild in self.guilds]
        print('{} has connected to guilds: {}'.format(self.user, ', '.join(guild_names)))
        if 'background' in self.settings.behaviors:
            self.loop.create_task(self.background_process())
        if 'timed' in self.settings.behaviors:
            self.loop.create_task(self.run_timed_events())
        if 'calendar' in self.settings.behaviors:
            self.loop.create_task(self.run_calendar_events())


    async def update_lookups(self, channel):
        message_history = await channel.history(limit=1).flatten()
        message = message_history[-1]
        self.last_timestamps[channel] = message.created_at
        last_senders = self.last_senders[channel.id] if channel.id in self.last_senders else None
        if last_senders is None:
            message_history = await channel.history(limit=50).flatten()
            last_senders  = [member.id for member in message.guild.members]
            last_senders += [msg.author.id for msg in message_history[::-1]]
        else:
            last_senders += [message.author.id]

        last_senders = list(dict.fromkeys(reversed(last_senders)))
        if self.user.id in last_senders:
            last_senders.remove(self.user.id)

        self.last_senders[channel.id] = last_senders

        member2var = {str(member): '<P{}>'.format(m+1) for m, member in enumerate(last_senders)}
        member2var[str(self.user.id)] = '<S>'        
        var2member = {v: '<@!{}>'.format(k) for k, v in member2var.items()}

        # duplicate var2members in case vars > members
        num_vars = len(var2member)-1
        for v in range(num_vars+1, 25):
            var2member['<P{}>'.format(v)] = var2member['<P{}>'.format(1+(v-1)%num_vars)]

        self.member2var = member2var
        self.var2member = var2member


    async def run_program(self, program, data, channel, program_idx=0, reply_probability=0):
        response, embed, file = '', None, None
        
        # get settings
        settings = self.settings.programs[program]
        settings = [settings] if not isinstance(settings, list) else settings
        settings = settings[program_idx]

        # select program from gpt3 search
        if program == 'search':
            message = data
            candidates = [opt['document'] for opt in settings.options]
            query = re.sub('<@!?[0-9]+>', '', message.content).strip()
            result = gpt3.search(candidates, query, engine='curie')
            scores = [doc['score'] for doc in result['data']]
            ranked_queries = list(reversed(np.argsort(scores)))
            options_search = [{'candidate': candidates[idx], 'score': scores[idx]} 
                              for idx in ranked_queries]
            for result in options_search[:2]:
                print(" -> %s : %0.2f" % (result['candidate'], result['score']))
            idx_top = ranked_queries[0]
            program = settings.options[idx_top]['program']
            program_idx = 0 if 'program_idx' not in settings.options[idx_top] else settings.options[idx_top]['program_idx']
            settings = self.settings.programs[program]
            settings = [settings] if not isinstance(settings, list) else settings
            settings = settings[program_idx]

        # select program by first keyword
        elif program == 'keyword':
            message = data
            query = re.sub('<@!?[0-9]+>', '', message.content.lower()).strip().split(' ')
            if not query:
                return
            for p in settings.programs:
                if query[0] in p['keywords']:
                    program = p['program']
            if not program:
                return
            settings = self.settings.programs[program]
            

        ##########################################
        ## GPT-3 chat
        ##########################################
        
        if program == 'gpt3_chat':
            message = data
            response = await gpt3_chat.run(
                settings, 
                message, 
                channel, 
                self.member2var, 
                self.var2member)
            
            
        ##########################################
        ## GPT-3 single prompt
        ##########################################

        elif program == 'gpt3_prompt':
            message = data
            response = await gpt3_prompt.run(
                settings,
                message)

        
        ##########################################
        ## Calendar                    
        ##########################################

        elif program == 'calendar_notify':
            response = calendar.run(
                settings, 
                data)

 
        ##########################################
        ## Spotify                    
        ##########################################

        elif program == 'spotify':
            message = data
            response, image_url = spotify.run(message, self.user.id)
            if image_url:
                embed = discord.Embed()
                embed.set_image(url=image_url)


        ##########################################
        ## If this then that                    
        ##########################################

        elif program == 'ifttt':
            message = data
            response = ifttt.run(settings, message)


        ##########################################
        ## Instagram                    
        ##########################################

        elif program == 'instagram':
            message = data
            response = instagram.run(message)
            

        ##########################################
        ## Constant                    
        ##########################################

        elif program == 'constant':
            message = data
            response = 'gm'
            

        ##########################################
        ## ml4a
        ##########################################

        elif program == 'ml4a':  
            from programs import ml4a_client
            message = data
            if message is not None:
                await channel.send('<@!{}> Drawing something, give me a few minutes...'.format(message.author.id))
            local_filename = ml4a_client.run(settings)
            file = discord.File(local_filename, filename=local_filename)
            if message is not None:
                response = '<@!{}>'.format(message.author.id)


        ##########################################
        ## custom program to be written
        ## in superclass by end-user
        ##########################################

        else:
            message = None
            response, embed, file = await self.run_program_custom(program, data, settings)
            
            
        # if set to mention some users randomly
        if 'mention_random_users' in settings:
            await self.update_lookups(channel)
            num_mentions = random.randint(*settings.mention_random_users)
            members = list(set(self.member2var.keys()))
            random.shuffle(members)
            num_mentions = min(num_mentions, len(members))
            members = members[0:num_mentions]
            mentions = ' '.join(['<@!{}>'.format(m) for m in members])
            response = '{} {}'.format(mentions, response)

        # truncate to Discord max character limit
        if response is None:
            return
        response = response[:2000]
        
        # send to discord
        if random.random() < reply_probability and message is not None:
            await message.reply(response, embed=embed, file=file)
        else:
            await channel.send(response, embed=embed, file=file)


    async def run_program_custom(self, program, data, settings):
        await asyncio.sleep(0)
        return 'None', None, None


    async def add_reaction(self, message):
        last_message = re.sub('<@!?[0-9]+>', '', message.content)
        candidates = list(emoji_docs.keys())
        if last_message in emoji_search_results:
            result = emoji_search_results[last_message]
        else:
            result = gpt3.search(candidates, last_message, engine='curie')
            emoji_search_results[last_message] = result
        if 'data' not in result or len(result['data']) == 0:
            return
        scores = [doc['score'] for doc in result['data']]
        ranked_queries = list(reversed(np.argsort(scores)))
        ranked_candidates = [candidates[idx] for idx in ranked_queries]
        top_candidate = ranked_candidates[0]
        reaction = random.choice(emoji_docs[top_candidate]).strip()
        options = [{'candidate': candidates[idx], 'score': scores[idx]} 
                   for idx in ranked_queries
                   if scores[idx] > 20][:4]
        if len(options) == 0:
            return
        selected = random.choices([o['candidate'] for o in options], 
                                  weights=[o['score'] for o in options], k=1)[0]
        reaction = random.choice(emoji_docs[selected]).strip()
        await message.add_reaction(reaction)

    
    async def on_message(self, message):
        if not self.ready:
            return

        # mentions and metadata
        private = isinstance(message.channel, discord.channel.DMChannel)
        all_mentions = re.findall('<@!?([0-9]+)>', message.content)
        mentioned = str(self.user.id) in all_mentions
        author_is_self = message.author.id == self.user.id

        # if it's a reply, check if reply is to self        
        if message.reference:
            prev_msg = await message.channel.fetch_message(message.reference.message_id)
            reply_to_is_self = prev_msg.author.id == self.user.id
            mentioned = mentioned or reply_to_is_self
            
        # which contexts (on_message, on_mention, or background)
        behavior = self.settings.behaviors
        if private:
            contexts = behavior.direct_message if 'direct_message' in behavior else None
        elif mentioned:
            contexts = behavior.on_mention if 'on_mention' in behavior else None
        else:
            contexts = behavior.on_message if 'on_message' in behavior else None
            
        # lookup & replace tables from member id's to variables e.g. <P1>, <S>
        if not private:
            await self.update_lookups(message.channel)
        else:
            self.member2var = {str(message.author.id): '<P1>', str(self.user.id): '<S>'}
            self.var2member = {'<P1>': '<@!{}>'.format(message.author.id), '<S>': '<@!{}>'.format(self.user.id)}

        # if no behavior for this trigger, stop
        if contexts is None:
            return
        
        contexts = contexts if isinstance(contexts, list) else [contexts]

        for context in contexts:
            
            # does it require a message trigger?
            if 'message_trigger' in context:
                if message.content.strip().lower() != context.message_trigger:
                    continue

            # maybe add a reaction to the message
            if reactions_enabled \
            and not author_is_self \
            and 'reaction_probability' in context \
            and (random.random() < context.reaction_probability):
                await self.add_reaction(message)

            # skipping conditions
            busy = len(self.timestamps) > 0
            decide_to_reply = (random.random() < context.response_probability)
            if private:
                channel_eligible = (message.author.id in context.members) if context.members else True
            else:
                channel_eligible = (message.channel.id in context.channels) if context.channels else True

            # if any skipping conditions are True, stop
            if busy \
            or author_is_self \
            or not decide_to_reply \
            or not channel_eligible:
                continue
            
            # bot has decided to reply; add timestamp and delay
            delay = context.delay[0]+(context.delay[1]-context.delay[0])*random.random() if 'delay' in context else 0
            timestamp = {"time": time.time(), "delay": delay}
            self.timestamps.append(timestamp)

            # select program
            program = context.program if 'program' in context else None
            if not program:
                print('No program selected')
                continue

            # choose program index if there are multiple and set args
            data = message
            channel = message.channel
            program_idx = context.program_index if 'program_index' in context else 0
            reply_probability = context.reply_probability if 'reply_probability' in context else 0
            
            # delay, run program, remove active timestamp
            await asyncio.sleep(
                timestamp['delay']
            )
            await self.run_program(
                program, 
                data,
                channel,
                program_idx=program_idx,
                reply_probability=reply_probability
            )
            self.timestamps.remove(timestamp)


    async def run_timed_events(self):
        await self.wait_until_ready()
        
        if len(self.settings.behaviors.timed) == 0:
            return
        
        latitude = float(os.getenv('LOCAL_LATITUDE'))
        longitude = float(os.getenv('LOCAL_LONGITUDE'))
        sun = Sun(latitude, longitude)

        while True:
            now = datetime.now()
            sunrise = utc_to_local(sun.get_sunrise_time()).replace(tzinfo=None)
            sunset = utc_to_local(sun.get_sunset_time()).replace(tzinfo=None)
            timed_events = []
            for t in self.settings.behaviors.timed:
                if t.type == 'daily':
                    target_time = now.replace(hour=t.time[0], minute=t.time[1], second=0)
                elif t.type == 'sunrise':
                    target_time = sunrise - timedelta(seconds=t.minutes_before * 60)
                elif t.type == 'sunset':
                    target_time = sunset - timedelta(seconds=t.minutes_before * 60)
                while target_time < now:
                    target_time += timedelta(days=1)
                timed_events.append({'event': t, 'time': target_time})
            timed_events = sorted(timed_events, key=lambda k: k['time']) 
            next_event = timed_events[0]
            channel_id = next_event['event'].channel
            program_idx = next_event['event'].program_index if 'program_index' in next_event['event'] else 0
            time_until = next_event['time'] - now
            print('time until next event: {}'.format(time_until))
            await asyncio.sleep(time_until.seconds)
            await self.run_program(
                next_event['event'].program, 
                data=None, 
                channel=self.get_channel(channel_id), 
                program_idx=program_idx)
            await asyncio.sleep(60)
            
      
    async def run_calendar_events(self):
        await self.wait_until_ready()

        calendar_settings = self.settings.behaviors.calendar
        calendar.setup(calendar_settings)
        time_before = calendar_settings.minutes_before * 60  # how long before event to notify
        check_every = calendar_settings.check_every * 60     # how often to check for events in loop

        while True:
            upcoming_events = calendar.get_upcoming_events()  

            if not upcoming_events:
                await asyncio.sleep(check_every)  # try again later
                continue   

            t0 = time.time()
            for event in upcoming_events:
                channel_id = calendar_settings.channel
                channel = self.get_channel(channel_id)
                data = EasyDict({
                    'id': event.event.id,
                    'summary': event.event.summary, 
                    'start': event.event.start, 
                    'end': event.event.end, 
                    'location': event.event.location,
                    'description': event.event.description
                })
                elapsed = time.time() - t0
                seconds_until_alert = event.time_until.total_seconds() - elapsed - time_before
                await asyncio.sleep(seconds_until_alert)
                await self.run_program(calendar_settings.program, data, channel)
                await asyncio.sleep(5)

    
    async def background_process(self):
        await self.wait_until_ready()
        
        background = self.settings.behaviors.background         
        channel = self.get_channel(background.channel)
        program_index = background.program_index if 'program_index' in background else 0
        
        while not self.is_closed():

            # get last message timestamp in the channel
            if channel in self.last_timestamps:
                last_message_time = self.last_timestamps[channel]
            
            # if none found, initialize lookups for that channel
            else:
                messages = await channel.history(limit=1).flatten()
                last_message = [msg for msg in messages][0]
                last_message_time = last_message.created_at
                await self.update_lookups(last_message.channel)
                last_message_time = self.last_timestamps[channel]

            # if not enough time has elapsed since last message, skip
            now = datetime.now()
            min_minutes_idle = background.min_minutes_idle
            minutes_since_last_message = (now-last_message_time).total_seconds() / 60
            if minutes_since_last_message < min_minutes_idle:
                continue

            # start with decaying probability of skipping (not posting) per minute
            prob_skip = 1.0 - background.probability_trigger
            if 'probability_skip_halflife' in background:
                halflives = minutes_since_last_message / background.probability_skip_halflife
                prob_skip = prob_skip * math.pow(0.5, halflives)

            # probability of trigger goes up over time
            prob_trigger = 1.0 - math.pow(prob_skip, 1.0 / background.every_num_minutes)

            if (random.random() < prob_trigger):
                await self.run_program(background.program,
                                       None, 
                                       channel, 
                                       program_idx=program_index)

            # run once every 55-65 seconds
            # the random drift helps spread out gpt3 requests/simultaneous posts
            await asyncio.sleep(60+random.randint(-5, 5))


def main(): 
    load_dotenv()
    intents = discord.Intents.default()
    intents.members = True
    loop = asyncio.get_event_loop()
    for botname in botlist:
        client = DiscordBot(intents=intents)
        loop.create_task(client.setup(bots[botname]))
    loop.run_forever()


if __name__ == "__main__":
    main()
