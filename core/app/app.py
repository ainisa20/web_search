import asyncio
import logging
import sys
import time
from quart import Quart, request, jsonify
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
import aiohttp
from typing import List, Dict
import async_timeout
import requests
import mechanicalsoup
from urllib.parse import urlparse
from langchain_community.utilities import SearxSearchWrapper
import os
import trafilatura
import functools  
import random

app = Quart(__name__)

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# 采用firecrawl
API_KEY = "test"  # 替换为真实 API Key
CRAWL_API_KEY = f"fc-{API_KEY}"
FIRE_CRAWL_BASE = os.getenv('FIRE_CRAWL_BASE', 'http://host.docker.internal:3002/v1')

## 采用jina reader
READER_SERVICE_URL = os.getenv('READER_SERVICE_URL', 'http://host.docker.internal:3010')

# ------- 单个抓取方法：trafilatura -------
async def fetch_url_trafilatura(session, url, timeout=5, format="markdown"):
    try:
        async with asyncio.timeout(timeout):
            loop = asyncio.get_event_loop()
            downloaded = await loop.run_in_executor(None, functools.partial(trafilatura.fetch_url, url))
            if not downloaded:
                logging.error(f"[trafilatura] Failed to fetch content from {url}")
                return None
            extract_func = functools.partial(
                trafilatura.extract,
                downloaded,
                output_format="markdown" if format.lower() == "markdown" else None
            )
            content = await loop.run_in_executor(None, extract_func)
            return content
    except Exception as e:
        logging.error(f"[trafilatura] Error processing {url}: {str(e)}")
        return None


# ------- 单个抓取方法：jina reader -------
async def fetch_url_jina(session, url, timeout=5, format="markdown"):
    target_url = f"{READER_SERVICE_URL}/{url}"
    headers = {
        "X-Respond-With": format
    }
    try:
        async with async_timeout.timeout(timeout):
            async with session.get(target_url, headers=headers, allow_redirects=True) as response:
                response.raise_for_status()
                return await response.text()
    except Exception as e:
        logging.error(f"[jina] Error processing {url}: {str(e)}")
        return None


# ------- 单个抓取方法：firecrawl API -------
async def fetch_url_firecrawl(session, url, timeout=5, formats=None):
    endpoint = 'scrape'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {CRAWL_API_KEY}'
    }
    data = {
        'url': url,
        'formats': formats or ['markdown'],
        'onlyMainContent': True
    }
    try:
        async with async_timeout.timeout(timeout):
            async with session.post(
                f"{FIRE_CRAWL_BASE}/{endpoint}",
                headers=headers,
                json=data
            ) as response:
                response.raise_for_status()
                result = await response.json()
                if not result.get('success', True):
                    logging.error(f"[firecrawl] API error for {url}: {result.get('message', 'Unknown')}")
                    return None
                return result.get('data', {}).get('markdown')
    except Exception as e:
        logging.error(f"[firecrawl] Error processing {url}: {str(e)}")
        return None


# ------- 主调度器：按优先级顺序自动切换 -------
async def smart_fetch(session, url, timeout=5, format="markdown"):
    fetchers = [
        ("trafilatura", fetch_url_trafilatura),
        ("firecrawl", fetch_url_firecrawl),
        ("jina", fetch_url_jina),
    ]

    robot_keywords = ["unusual traffic", "captcha", "robot check", "detected unusual traffic"]

    for name, fetcher in fetchers:
        # --- 每次尝试前随机小等待 ---
        await asyncio.sleep(random.uniform(0.5, 2.0))  # 0.5秒到2秒之间随机
        # --------------------------------

        if fetcher == fetch_url_firecrawl:
            result = await fetcher(session, url, timeout=timeout, formats=[format])
        else:
            result = await fetcher(session, url, timeout=timeout, format=format)

        if result:
            if any(keyword.lower() in result.lower() for keyword in robot_keywords):
                logging.warning(f"[{name}] Robot detected for {url}, skipping...")
                continue
            return result, name

    logging.error(f"[all methods failed] Could not fetch {url}")
    return None, None



# ------- 并发处理多个 URL -------
async def fetch_all_urls(urls, timeout=5, format="markdown", global_delay=False):
    async with aiohttp.ClientSession() as session:
        tasks = []

        for url in urls:
            if global_delay:
                await asyncio.sleep(random.uniform(0.5, 1.5))  # 每个URL之前统一慢点
            tasks.append(smart_fetch(session, url, timeout, format=format))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        final_contents = []
        for idx, res in enumerate(results):
            url = urls[idx]
            if isinstance(res, Exception):
                logging.error(f"[fetch_all_urls] Error processing {url}: {str(res)}")
                final_contents.append(None)
            else:
                content, method = res
                if content:
                    print(f"✅ Successfully fetched [{url}] using [{method}]")
                    final_contents.append(content)
                else:
                    print(f"❌ Failed to fetch [{url}] with all methods")
                    final_contents.append(None)

        return final_contents


SEARX_HOST = os.getenv('SEARX_HOST', 'http://host.docker.internal:8081')


def format_search_results(searx_results):
    search_results = []
    for result in searx_results:
        title = result.get("title", "No Title")
        link = result.get("url", "")
        snippet = result.get("content", "")
        search_results.append({
            "title": title,
            "url": link,
            "snippet": snippet,
        })
    return search_results

def searxng_search(
    keyword=None,
    num_results=5,
    categories="general",
    time_range="day",
    engines="360search,bing,baidu,quark,sogou",
    format="json",
    searxng_host=SEARX_HOST
):
    if not keyword:
        return []

    try:
        url = f"{searxng_host}/search"
        params = {
            "q": keyword,
            "format": format,
            "language": "zh",
            "categories": categories,
            "time_range": time_range,
            "engines": engines,
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()

        results_dict = response.json()
        results_list = results_dict.get("results", [])

        # 打印原始返回
        print("Raw searxng response:", results_dict)

        # 取前 num_results 个
        results_list = results_list[:num_results]

        search_results = format_search_results(results_list)

        # 打印格式化后的结果
        print("searxng的搜索结果:", search_results)

        return search_results

    except Exception as e:
        print(f"Error requesting SearxNG search: {e}")
        logging.error(f"Error requesting SearxNG search: {e}")
        return []

def sou(keyword=None):
    if not keyword:
        return []

    search_url = "http://www.baidu.com/s"
    params = {'wd': keyword}

    # 模拟正常浏览器请求的 headers，防止反爬虫检测
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 13_3 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.0.4 Mobile/15E148 Safari/604.1"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }

    try:
        response = requests.get(search_url, params=params, headers=headers, timeout=5, allow_redirects=True)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        search_results = []
        for result in soup.find_all('h3'):
            title = result.get_text().strip()
            link_tag = result.find('a', href=True)
            link = link_tag['href'] if link_tag else ""

            # 解析重定向的URL
            if urlparse(link).scheme != "":
                link2 = requests.head(link).headers.items()
                if "Location" in dict(link2):
                    link = dict(link2)["Location"]

            snippet = ""  # 设置空的 snippet

            search_results.append({
                "title": title,
                "url": link,
                "snippet": snippet
            })

        return search_results

    except requests.RequestException as e:
        logging.error(f"Error requesting Baidu search: {e}")
        return []


def go(keyword=None):
    # if len(sys.argv) > 1:
    #     keyword = " ".join(sys.argv[1:])
    if not keyword:
        return []  # 返回空列表而不是字典

    try:
        browser = mechanicalsoup.StatefulBrowser()
        url = f"https://cn.bing.com/search?q={keyword}"
        browser.open(url)
        page = browser.get_current_page()

        results = []
        for element in page.find_all("h2"):
            link = element.find("a")['href']
            text = element.find("a").text

            # 将结果格式化为字典，并添加空的 snippet
            results.append({
                "title": text,
                "url": link,
                "snippet": ""  # 添加空的虚拟 snippet
            })

        # print("bing的搜索结果:", results)

        return results

    except Exception as e:
        logging.error(f"Error requesting Bing search: {e}")
        return []  # 返回空列表


search_type = os.getenv('SEARCH_TYPE', 'searxng')


async def search_engines(keyword):
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=4) as executor:
        # 使用 run_in_executor 提交异步任务
        future_searxng = loop.run_in_executor(executor, searxng_search, keyword)
        future_sou = loop.run_in_executor(executor, sou, keyword)
        future_go = loop.run_in_executor(executor, go, keyword)

        # 创建一个结果列表
        results = []

        if search_type == 'searxng':
            futures = [future_searxng]
        elif search_type == 'sougo':
            futures = [future_sou, future_go]
        elif search_type == 'mutil':
            futures = [future_searxng, future_sou, future_go]
        else:
            raise ValueError(f"Unsupported SEARCH_TYPE: {search_type}")

        for future in await asyncio.gather(*futures, return_exceptions=True):
            if isinstance(future, list):
                # 如果返回的是一个列表，说明是有效的结果
                results.extend(future)
            else:
                # 如果返回的是其他类型或出现异常，记录错误
                logging.error(f"搜索函数返回了意外的数据类型或发生了错误: {type(future)} - 错误信息: {future}")

        return results


def summarize_results(response_results: List[Dict]) -> List[Dict]:
    summarized_results = []
    for result in response_results:
        if 'url' in result and 'title' in result and 'content' in result and result['content']:
            summarized_result = {
                'title': result['title'],
                'content': result['content'],
                'snippet': result.get('snippet', ''),  # 添加 snippet 字段，默认为空
                'url': result['url']  # 添加url字段
            }
            summarized_results.append(summarized_result)
    return summarized_results


global_summarized_results = []


@app.route('/v1/web_search', methods=['POST'])
async def my_web_search():
    try:
        global global_summarized_results

        # 获取 JSON 数据，并检查是否为空
        data = await request.get_json(silent=True)
        if not data:
            return jsonify({"error": "无效的请求，缺少 JSON 数据"}), 400

        logging.info(f"Received data: {data}")
        keyword = data.get('keyword')
        if not keyword:
            return jsonify({"error": "未提供关键词"}), 400

        # 执行搜索
        results = await search_engines(keyword)
        logging.info(f"Search results: {results}")

        if not results:
            return jsonify({"error": "未找到搜索结果"}), 404

        # 处理搜索结果
        url_to_result_map = {result['url']: result for result in results if 'url' in result}
        urls = list(url_to_result_map.keys())
        contents = await fetch_all_urls(urls)

        response_results = []
        snippets = []
        for url, content in zip(urls, contents):
            title = url_to_result_map[url].get('title', '无标题')
            snippet = url_to_result_map[url].get('snippet', '')
            url_to_result_map[url]['content'] = content if content else ''
            url_to_result_map[url]['snippet'] = snippet

            response_results.append(url_to_result_map[url])
            snippets.append(snippet)

        summarized_results = summarize_results(response_results)
        global_summarized_results = summarized_results

        results_content = "\n".join([
            # f"# 标题: {res['title']}, ## 内容: {res['content']}, ### 链接: {res['url']}"
            f"# 标题: {res['title']}, ## 内容: {res['content']}"
            for res in summarized_results
        ])
        # results_snippet = "\n".join(snippets)

        # results_all = results_content + "\n" + results_snippet

        # print(f"汇总的搜索结果: \n{results_all}")

        # return results_content + "\n" + results_snippet, 200, {'Content-Type': 'text/plain; charset=utf-8'}
        return results_content.strip(), 200, {
            'Content-Type': 'text/plain; charset=utf-8'}


    except Exception as e:
        logging.error(f"请求处理时出错: {e}")
        return jsonify({"error": "内部服务器错误"}), 500

@app.route('/v2/web_search', methods=['POST'])
async def my_web_search2():
    try:
        global global_summarized_results

        # 获取 JSON 数据，并检查是否为空
        data = await request.get_json(silent=True)
        if not data:
            return jsonify({"error": "无效的请求，缺少 JSON 数据"}), 400

        logging.info(f"Received data: {data}")
        keyword = data.get('keyword')
        if not keyword:
            return jsonify({"error": "未提供关键词"}), 400

        # 执行搜索
        results = await search_engines(keyword)
        logging.info(f"Search results: {results}")

        if not results:
            return jsonify({"error": "未找到搜索结果"}), 404

        # 处理搜索结果
        url_to_result_map = {result['url']: result for result in results if 'url' in result}
        urls = list(url_to_result_map.keys())
        contents = await fetch_all_urls(urls)

        response_results = []
        snippets = []
        for url, content in zip(urls, contents):
            title = url_to_result_map[url].get('title', '无标题')
            snippet = url_to_result_map[url].get('snippet', '')
            url_to_result_map[url]['content'] = content if content else ''
            url_to_result_map[url]['snippet'] = snippet

            response_results.append(url_to_result_map[url])
            snippets.append(snippet)

        summarized_results = summarize_results(response_results)
        global_summarized_results = summarized_results

        results_content = "\n".join([
            f"# 标题: {res['title']}"
            for res in summarized_results
        ])
        results_snippet = "\n".join(snippets)

        results_all = results_content + "\n" + results_snippet

        # print(f"汇总的搜索结果: \n{results_all}")

        # return results_content + "\n" + results_snippet, 200, {'Content-Type': 'text/plain; charset=utf-8'}
        return results_content.strip(), 200, {
            'Content-Type': 'text/plain; charset=utf-8'}


    except Exception as e:
        logging.error(f"请求处理时出错: {e}")
        return jsonify({"error": "内部服务器错误"}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5045)
