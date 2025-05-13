import aiohttp
import io
import discord
from discord.ext import commands
import asyncio
import yt_dlp
import os
import random
from discord import File
import requests
from dotenv import load_dotenv

load_dotenv()


from dotenv import load_dotenv
load_dotenv()
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")  # .env 파일에 YOUTUBE_API_KEY=your_key 형식으로 작성

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.presences = True

bot = commands.Bot(command_prefix='!', intents=intents)

ytdl_opts = {
    'format': 'bestaudio/best',
    'noplaylist': False,
    'quiet': True,
    # 'cookiefile': os.getenv('LOCAL_URL'),  # 쿠키 파일 경로 (본인 서버에 맞게 수정)
    # 'cookiefile': os.getenv('SERVER_URL'),  # 쿠키 파일 경로 (본인 서버에 맞게 수정)
    'source_address': '0.0.0.0'
}

ffmpeg_opts = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

song_queue = {}
autoplay_enabled = {}
last_played_url = {}
playing_status = {}
voice_clients = {}

def get_youtube_urls(search):
    with yt_dlp.YoutubeDL(ytdl_opts) as ydl:
        try:
            if not search.startswith("http"):
                search = f"ytsearch:{search}"
            info = ydl.extract_info(search, download=False)
            if 'entries' not in info:
                return [(info['url'], info['title'])]
            return [(entry['url'], entry['title']) for entry in info['entries'] if entry]
        except Exception as e:
            print(f"[ERROR] 유튜브 링크 추출 실패: {e}")
            return []

def get_related_video_url(video_url):
    try:
        if "watch?v=" not in video_url:
            return None
        video_id = video_url.split("watch?v=")[1].split("&")[0]
        api_url = (
            f"https://www.googleapis.com/youtube/v3/search"
            f"?part=snippet&type=video&maxResults=5&relatedToVideoId={video_id}&key={YOUTUBE_API_KEY}"
        )
        response = requests.get(api_url)
        if response.status_code == 403:
            print("[ERROR] YouTube API 무료 할당량 초과")
            return "quota_exceeded"
        data = response.json()
        items = data.get("items", [])
        if not items:
            return None
        related_video_id = items[0]["id"]["videoId"]
        return f"https://www.youtube.com/watch?v={related_video_id}"
    except Exception as e:
        print(f"[ERROR] API 관련곡 검색 실패: {e}")
        return None

async def play_next(ctx, guild_id):
    if song_queue[guild_id]:
        url, title = song_queue[guild_id].pop(0)
        try:
            source = await discord.FFmpegOpusAudio.from_probe(url, **ffmpeg_opts)
            last_played_url[guild_id] = url
        except Exception as e:
            await ctx.send(f"❗ 음악 스트림 오류: {e}")
            print(f"[ERROR] 스트리밍 오류: {e}")
            await play_next(ctx, guild_id)
            return

        vc = voice_clients[guild_id]

        def after_play(error):
            if error:
                print(f"[ERROR] after_play 예외: {error}")
            fut = asyncio.run_coroutine_threadsafe(play_next(ctx, guild_id), bot.loop)
            try:
                fut.result()
            except Exception as e:
                print(f"[ERROR] 재생 후 처리 실패: {e}")

        vc.play(source, after=after_play)
        await ctx.send(f"🎵 재생 중: **{title}**")
    else:
        if autoplay_enabled.get(guild_id, False) and last_played_url.get(guild_id):
            next_url = get_related_video_url(last_played_url[guild_id])
            if next_url == "quota_exceeded":
                await ctx.send("❌ YouTube API 무료 할당량을 모두 사용했습니다. 자동재생이 중단됩니다.")
                autoplay_enabled[guild_id] = False
                return
            if next_url:
                song_queue[guild_id].append((next_url, "Auto: Related Video"))
                await ctx.send("🔁 관련 곡을 자동으로 재생합니다.")
                await play_next(ctx, guild_id)
                return
            else:
                await ctx.send("⚠️ 관련곡을 찾을 수 없습니다. 자동재생 종료.")
        playing_status[guild_id] = False
        if voice_clients[guild_id].is_connected():
            await voice_clients[guild_id].disconnect()
        await ctx.send("📭 대기열 종료. 채널 퇴장.")

@bot.command()
async def play(ctx, *, search):
    if not ctx.author.voice:
        await ctx.send("❗ 먼저 음성 채널에 들어가세요.")
        return

    guild_id = ctx.guild.id
    tracks = get_youtube_urls(search)
    if not tracks:
        await ctx.send("❌ 유튜브에서 노래를 찾을 수 없습니다.")
        return

    song_queue.setdefault(guild_id, [])
    playing_status.setdefault(guild_id, False)

    for url, title in tracks:
        song_queue[guild_id].append((url, title))
    await ctx.send(f"✅ {len(tracks)}곡 대기열에 추가됨.")

    if not playing_status[guild_id]:
        try:
            channel = ctx.author.voice.channel
            vc = await channel.connect(reconnect=True, timeout=60)
            voice_clients[guild_id] = vc
            playing_status[guild_id] = True
            last_played_url[guild_id] = tracks[0][0]
            await play_next(ctx, guild_id)
        except Exception as e:
            await ctx.send(f"❌ 음성 채널 연결 실패: {e}")
            print(f"[ERROR] 연결 실패: {e}")

@bot.command()
async def autoplay(ctx, mode: str = None):
    guild_id = ctx.guild.id
    if mode == "on":
        autoplay_enabled[guild_id] = True
        await ctx.send("🔁 자동 관련 음악 재생이 **활성화**되었습니다.")
    elif mode == "off":
        autoplay_enabled[guild_id] = False
        await ctx.send("⏹ 자동 관련 음악 재생이 **비활성화**되었습니다.")
    else:
        status = autoplay_enabled.get(guild_id, False)
        await ctx.send(f"📢 자동재생 상태: {'켜짐 🔁' if status else '꺼짐 ⛔'}\n`!autoplay on` 또는 `!autoplay off` 로 설정하세요.")

@bot.command()
async def queue(ctx):
    guild_id = ctx.guild.id
    if guild_id not in song_queue or not song_queue[guild_id]:
        await ctx.send("📭 대기열이 비었습니다.")
        return

    message = "**🎶 대기열 목록:**\n"
    for i, (_, title) in enumerate(song_queue[guild_id], 1):
        message += f"{i}. {title}\n"
    await ctx.send(message)

@bot.command()
async def skip(ctx):
    guild_id = ctx.guild.id
    if guild_id in voice_clients and voice_clients[guild_id].is_playing():
        voice_clients[guild_id].stop()
        await ctx.send("⏭ 현재 곡을 스킵했습니다.")
    else:
        await ctx.send("⚠️ 현재 재생 중인 곡이 없습니다.")

@bot.command()
async def pause(ctx):
    guild_id = ctx.guild.id
    if guild_id in voice_clients and voice_clients[guild_id].is_playing():
        voice_clients[guild_id].pause()
        await ctx.send("⏸ 재생을 일시정지했습니다.")
    else:
        await ctx.send("⚠️ 재생 중이 아닙니다.")

@bot.command()
async def resume(ctx):
    guild_id = ctx.guild.id
    if guild_id in voice_clients and voice_clients[guild_id].is_paused():
        voice_clients[guild_id].resume()
        await ctx.send("▶️ 재생을 다시 시작했습니다.")
    else:
        await ctx.send("⚠️ 일시정지 상태가 아닙니다.")

@bot.command()
async def stop(ctx):
    guild_id = ctx.guild.id
    if guild_id in voice_clients and voice_clients[guild_id].is_connected():
        await voice_clients[guild_id].disconnect()
        song_queue[guild_id] = []
        playing_status[guild_id] = False
        await ctx.send("🛑 재생 중단 및 채널 퇴장.")
    else:
        await ctx.send("❗ 봇이 음성 채널에 있지 않습니다.")

@bot.command(name='p')
async def _p(ctx, *, search):
    await play(ctx, search=search)

@bot.command(name='q')
async def _q(ctx):
    await queue(ctx)

@bot.command(name='s')
async def _s(ctx):
    await skip(ctx)

@bot.command(name='pa')
async def _pa(ctx):
    await pause(ctx)

@bot.command(name='r')
async def _r(ctx):
    await resume(ctx)

@bot.command(name='st')
async def _st(ctx):
    await stop(ctx)


@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    guild_id = member.guild.id
    vc = voice_clients.get(guild_id)

    if vc and vc.is_connected():
        # 봇이 혼자 남았는지 확인
        if len(vc.channel.members) == 1:
            await vc.disconnect()
            playing_status[guild_id] = False
            song_queue[guild_id] = []
            print(f"🔇 자동 퇴장 - 서버 {guild_id}")

@bot.event
async def on_ready():
    print(f'{bot.user} 로 로그인했습니다!')
    activity = discord.Game(name='!help 입력해보세요')
    await bot.change_presence(status=discord.Status.online, activity=activity)

@bot.command(name='cat', aliases=['고양이', '냥이'])
async def random_cat(ctx):
    url = "https://cataas.com/cat"  # 고양이 이미지 API

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return await ctx.send("고양이 이미지를 가져올 수 없어요 😿")
            data = await resp.read()
            await ctx.send(file=discord.File(fp=io.BytesIO(data), filename="cat.jpg"))

@bot.command(name='mung', aliases=['뭉탱이', '뭉', '케인'])
async def send_random_mung(mtx):
    mung_folder = './mung'  # 뭉탱이 사진이 들어있는 폴더 경로
    images = [f for f in os.listdir(mung_folder) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif'))]

    if not images:
        await mtx.send("얘! 뭉탱이 사진이 없단다!")
        return

    selected = random.choice(images)
    file_path = os.path.join(mung_folder, selected)
    await mtx.send(file=File(file_path))

bot.help_command = None  # 기본 help 명령어를 비활성화

@bot.command(name='help')
async def help_command(ctx):
    help_text = """
🎵 **음악 봇 명령어 목록**

`!play [검색어/링크]` 또는 `!p [검색어/링크]` — 음악을 재생하거나 대기열에 추가  
`!queue` 또는 `!q` — 현재 대기열 보기  
`!skip` 또는 `!s` — 현재 재생 중인 음악을 건너뜀  
`!pause` 또는 `!pa` — 음악을 일시정지  
`!resume` 또는 `!r` — 일시정지된 음악을 다시 재생  
`!stop` 또는 `!st` — 음악을 중지하고 봇이 음성 채널에서 퇴장  
`!cat` 또는 `!고양이`, `!냥이` — 랜덤고양이 이미지
`!mung` 또는 `!뭉탱이`, `!뭉` 또는 `!케인` — 랜덤뭉탱이 이미지
`!help` — 이 명령어 목록을 표시
"""
    await ctx.send(help_text)

bot.run(os.getenv('DISCORD_BOT_TOKEN'))
#bot.run("")  # <-- 여기에 실제 토큰을 넣어야 합니다