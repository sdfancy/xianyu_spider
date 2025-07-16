# --- START OF FILE ai_filter.py ---



import os

import requests

import base64

import json

import re

import time

import argparse

from functools import wraps

from openai import OpenAI, APIStatusError

from requests.exceptions import HTTPError

from dotenv import load_dotenv

from pprint import pprint



# --- 1. 配置和初始化 ---



# 加载 .env 文件中的环境变量

load_dotenv()



API_KEY = os.getenv("OPENAI_API_KEY")

BASE_URL = os.getenv("OPENAI_BASE_URL")

MODEL_NAME = os.getenv("OPENAI_MODEL_NAME")

NTFY_TOPIC_URL = os.getenv("NTFY_TOPIC_URL")



# 检查配置是否齐全

if not all([API_KEY, BASE_URL, MODEL_NAME]):

    print("错误：请确保在 .env 文件中完整设置了 OPENAI_API_KEY, OPENAI_BASE_URL 和 OPENAI_MODEL_NAME。")

    exit()



# 初始化 OpenAI 客户端

try:

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

except Exception as e:

    print(f"初始化 OpenAI 客户端时出错: {e}")

    exit()



# 定义目录和文件名

IMAGE_SAVE_DIR = "images"

os.makedirs(IMAGE_SAVE_DIR, exist_ok=True)

# 新增：定义用于保存进度的结果文件

RESULTS_FILE = "analysis_results.json"




# 定义下载图片所需的请求头

IMAGE_DOWNLOAD_HEADERS = {

    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:139.0) Gecko/20100101 Firefox/139.0',

    'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',

    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',

    'Connection': 'keep-alive',

    'Upgrade-Insecure-Requests': '1',

}




# --- 2. 辅助函数 (已更新) ---



def retry_on_failure(retries=3, delay=5):

    """

    一个通用的重试装饰器，增加了对HTTP错误的详细日志记录。

    """

    def decorator(func):

        @wraps(func)

        def wrapper(*args, **kwargs):

            for i in range(retries):

                try:

                    return func(*args, **kwargs)

                except (APIStatusError, HTTPError) as e:

                    print(f"函数 {func.__name__} 第 {i + 1}/{retries} 次尝试失败，发生HTTP错误。")

                    if hasattr(e, 'status_code'):

                        print(f"  - 状态码 (Status Code): {e.status_code}")

                    if hasattr(e, 'response') and hasattr(e.response, 'text'):

                        response_text = e.response.text

                        print(f"  - 返回值 (Response): {response_text[:300]}{'...' if len(response_text) > 300 else ''}")

                # 新增：捕获JSONDecodeError，使其也能触发重试

                except json.JSONDecodeError as e:

                    print(f"函数 {func.__name__} 第 {i + 1}/{retries} 次尝试失败: JSON解析错误 - {e}")

                except Exception as e:

                    print(f"函数 {func.__name__} 第 {i + 1}/{retries} 次尝试失败: {type(e).__name__} - {e}")

                

                if i < retries - 1:

                    print(f"将在 {delay} 秒后重试...")

                    time.sleep(delay)



            print(f"函数 {func.__name__} 在 {retries} 次尝试后彻底失败。")

            return None

        return wrapper

    return decorator



def parse_product_file(file_path='macbook_air_m1_full_data.jsonl'): # <-- 1. 更改默认文件名

    """解析商品JSONL文件，返回商品信息字典的列表。"""

    products = []

    try:

        with open(file_path, 'r', encoding='utf-8') as f:

            for line in f: # <-- 2. 逐行读取

                if line.strip(): # 确保不是空行

                    try:

                        products.append(json.loads(line)) # <-- 3. 解析JSON并添加到列表

                    except json.JSONDecodeError:

                        print(f"警告：跳过无法解析的行: {line.strip()}")

    except FileNotFoundError:

        print(f"错误：找不到文件 {file_path}")

        return []

    

    return products



@retry_on_failure(retries=2, delay=3)

def _download_single_image(url, save_path):

    """一个带重试的内部函数，用于下载单个图片，并使用自定义请求头。"""

    response = requests.get(url, headers=IMAGE_DOWNLOAD_HEADERS, timeout=20, stream=True)

    response.raise_for_status()

    with open(save_path, 'wb') as f:

        for chunk in response.iter_content(chunk_size=8192):

            f.write(chunk)

    return save_path



def download_all_images(product_id, image_urls):

    """下载一个商品的所有图片。如果图片已存在则跳过。"""

    if not image_urls:

        return []

    

    urls = [url.strip() for url in image_urls if url.strip().startswith('http')]

    if not urls:

        return []

    

    saved_paths = []

    total_images = len(urls)

    for i, url in enumerate(urls):

        try:

            clean_url = url.split('.heic')[0] if '.heic' in url else url

            file_name_base = os.path.basename(clean_url).split('?')[0]

            file_name = f"product_{product_id}_{i+1}_{file_name_base}"

            file_name = re.sub(r'[\\/*?:"<>|]', "", file_name)

            if not os.path.splitext(file_name)[1]:

                 file_name += ".jpg"



            save_path = os.path.join(IMAGE_SAVE_DIR, file_name)



            if os.path.exists(save_path):

                print(f"图片 {i+1}/{total_images} 已存在，跳过下载: {os.path.basename(save_path)}")

                saved_paths.append(save_path)

                continue



            print(f"正在下载图片 {i+1}/{total_images}: {url}")

            if _download_single_image(url, save_path):

                print(f"图片 {i+1}/{total_images} 已成功下载到: {os.path.basename(save_path)}")

                saved_paths.append(save_path)

        except Exception as e:

            print(f"处理图片 {url} 时发生错误，已跳过此图: {e}")

            

    return saved_paths



def encode_image_to_base64(image_path):

    """将本地图片文件编码为 Base64 字符串。"""

    if not image_path or not os.path.exists(image_path):

        return None

    try:

        with open(image_path, "rb") as image_file:

            return base64.b64encode(image_file.read()).decode('utf-8')

    except Exception as e:

        print(f"编码图片时出错: {e}")

        return None

@retry_on_failure(retries=3, delay=5)

def send_ntfy_notification(product_data, reason):

    """

    当发现推荐商品时，发送一个高优先级的 ntfy.sh 通知。

    """

    if not NTFY_TOPIC_URL:

        print("警告：未在 .env 文件中配置 NTFY_TOPIC_URL，跳过通知。")

        return



    title = product_data.get('商品标题', 'N/A')

    price = product_data.get('当前售价', 'N/A')

    link = product_data.get('商品链接', '#')



    # 构建通知消息体和标题

    message = f"价格: {price}\n原因: {reason}\n链接: {link}"

    notification_title = f"🚨 新推荐! {title[:30]}..."



    try:

        print(f"   -> 正在发送 ntfy 通知到: {NTFY_TOPIC_URL}")

        requests.post(

            NTFY_TOPIC_URL,

            data=message.encode('utf-8'),

            headers={

                "Title": notification_title.encode('utf-8'),

                "Priority": "urgent",  # 最高优先级

                "Tags": "bell,vibration" # 触发声音和振动

            },

            timeout=10

        )

        print("   -> 通知发送成功。")

    except Exception as e:

        print(f"   -> 发送 ntfy 通知失败: {e}")

        raise # 向上抛出异常以触发重试



@retry_on_failure(retries=5, delay=10)

def get_ai_analysis(product_data, image_paths=None):

    """

    将完整的商品JSON数据和所有图片发送给 AI 进行分析。

    """

    item_info = product_data.get('商品信息', {})

    product_id = item_info.get('商品ID', 'N/A')

    

    print(f"\n===== 正在分析商品 #{product_id} (含 {len(image_paths or [])} 张图片) =====")

    print(f"标题: {item_info.get('商品标题', '无')}")

    

    # 将整个商品数据结构格式化为JSON字符串

    product_details_json = json.dumps(product_data, ensure_ascii=False, indent=2)

    

    # [--- START OF MODIFICATION ---]

    # 对 system_prompt 进行了关键逻辑修正

    system_prompt = """

你是世界顶级的二手交易分析专家，代号 **EagleEye-V6.4**。你的核心任务是基于我提供的严格标准，对一个以JSON格式提供的商品信息进行深入的、基于用户画像的评估。你的分析必须极度严谨，并以一个结构化的JSON对象返回你的完整分析，不能有任何额外的文字。



### **第一部分：核心分析原则 (不可违背)**



1.  **画像优先原则 (PERSONA-FIRST PRINCIPLE) [V6.3 核心升级]**: 这是解决“高级玩家”与“普通贩子”识别混淆的最高指导原则。在评估卖家时，你的首要任务不是寻找孤立的疑点，而是**构建一个连贯的卖家“行为画像”**。你必须回答核心问题：“这个卖家的所有行为（买、卖、评价、签名）组合起来，讲述的是一个怎样的故事？”

    *   **如果故事是连贯的个人行为**（例如，一个热爱数码产品，不断体验、升级、出掉旧设备的发烧友），那么一些表面上的“疑点”（如交易频率略高）可以被合理解释，**不应**作为否决依据。

    *   **如果故事是矛盾的、不连贯的，或者明确指向商业行为**（例如，购买记录是配件和坏机，售卖记录却是大量“几乎全新”的同型号机器），那么即便卖家伪装得很好，也必须判定为商家。



2.  **一票否决硬性原则 (HARD DEAL-BREAKER RULES)**: 以下是必须严格遵守的否决条件。任何一项不满足，`is_recommended` 必须立即判定为 `false`。

    *   **型号/芯片**: 必须是 **MacBook Air** 且明确为 **M1 芯片**。

    *   **卖家信用**: `卖家信用等级` 必须是 **'卖家信用极好'**。

    *   **邮寄方式**: 必须 **支持邮寄**。

    *   **电池健康硬性门槛**: 若明确提供了电池健康度，其数值 **`必须 ≥ 90%`**。

    *   **【V6.4 逻辑修正】机器历史**: **不得出现**任何“维修过”、“更换过部件”、“有暗病”等明确表示有拆修历史的描述。



3.  **图片至上原则 (IMAGE-FIRST PRINCIPLE)**: 如果图片信息（如截图）与文本描述冲突，**必须以图片信息为最终裁决依据**。



4.  **【V6.4 逻辑修正】信息缺失处理原则 (MISSING-INFO HANDLING)**: 对于可后天询问的关键信息（特指**电池健康度**和**维修历史**），若完全未找到，状态应为 `NEEDS_MANUAL_CHECK`，这**不直接导致否决**。如果卖家画像极为优秀，可以进行“有条件推荐”。



---



### **第二部分：详细分析指南**



**A. 商品本身评估 (Criteria Analysis):**



1.  **型号芯片 (`model_chip`)**: 核对所有文本和图片。非 MacBook Air M1 则 `FAIL`。

2.  **电池健康 (`battery_health`)**: 健康度 ≥ 90%。若无信息，则为 `NEEDS_MANUAL_CHECK`。

3.  **成色外观 (`condition`)**: 最多接受“细微划痕”。仔细审查图片四角、A/D面。

4.  **【V6.4 逻辑修正】机器历史 (`history`)**: 严格审查所有文本和图片，寻找“换过”、“维修”、“拆过”、“进水”、“功能不正常”等负面描述。**若完全未提及，则状态为 `NEEDS_MANUAL_CHECK`**；若有任何拆修证据，则为 `FAIL`。



**B. 卖家与市场评估 (核心)**



5.  **卖家背景深度分析 (`seller_type`) - [决定性评估]**:

    *   **核心目标**: 运用“画像优先原则”，判定卖家是【个人玩家】还是【商家/贩子】。

    *   **【V6.3 升级】危险信号清单 (Red Flag List) 及豁免条款**:

        *   **交易频率**: 短期内有密集交易。

            *   **【发烧友豁免条款】**: 如果交易记录时间跨度长（如超过2年），且买卖行为能形成“体验-升级-出售”的逻辑闭环，则此条不适用。一个长期发烧友在几年内有数十次交易是正常的。

        *   **商品垂直度**: 发布的商品高度集中于某一特定型号或品类。

            *   **【发烧友豁免条款】**: 如果卖家是该领域的深度玩家（例如，从他的购买记录、评价和发言能看出），专注于某个系列是其专业性的体现。关键看他是在“玩”还是在“出货”。

        *   **“行话”**: 描述中出现“同行、工作室、拿货、量大从优”等术语。

            *   **【无豁免】**: 此为强烈的商家信号。

        *   **物料购买**: 购买记录中出现批量配件、维修工具、坏机等。

            *   **【无豁免】**: 此为决定性的商家证据。

        *   **图片/标题风格**: 图片背景高度统一、专业；或标题模板化。

            *   **【发烧友豁免条款】**: 如果卖家追求完美，有自己的“摄影棚”或固定角落来展示他心爱的物品，这是加分项。关键看图片传递的是“爱惜感”还是“商品感”。



6.  **邮寄方式 (`shipping`)**: 明确“仅限xx地面交/自提”则 `FAIL`。

7.  **卖家信用 (`seller_credit`)**: `卖家信用等级` 必须为 **'卖家信用极好'**。



---



### **第三部分：输出格式 (必须严格遵守)**



你的输出必须是以下格式的单个 JSON 对象，不能包含任何额外的注释或解释性文字。



```json

{

  "prompt_version": "EagleEye-V6.4",

  "is_recommended": boolean,

  "reason": "一句话综合评价。若为有条件推荐，需明确指出：'有条件推荐，卖家画像为顶级个人玩家，但需在购买前向其确认[电池健康度]和[维修历史]等缺失信息。'",

  "risk_tags": ["string"],

  "criteria_analysis": {

    "model_chip": { "status": "string", "comment": "string", "evidence": "string" },

    "battery_health": { "status": "string", "comment": "string", "evidence": "string" },

    "condition": { "status": "string", "comment": "string", "evidence": "string" },

    "history": { "status": "string", "comment": "string", "evidence": "string" },

    "seller_type": {

      "status": "string",

      "persona": "string",

      "comment": "【首要结论】综合性的结论，必须首先点明卖家画像。如果判定为FAIL，必须在此明确指出是基于哪个危险信号以及不符合的逻辑链。",

      "analysis_details": {

        "temporal_analysis": {

          "comment": "关于交易时间间隔和分布的分析结论。",

          "evidence": "例如：交易记录横跨数年，间隔期长，符合个人卖家特征。"

        },

        "selling_behavior": {

          "comment": "关于其售卖商品种类的分析。",

          "evidence": "例如：售卖商品多为个人升级换代的数码产品，逻辑自洽。"

        },

        "buying_behavior": {

          "comment": "【关键】关于其购买历史的分析结论。",

          "evidence": "例如：购买记录显示为游戏盘和生活用品，符合个人消费行为。"

        },

        "behavioral_summary": {

          "comment": "【V6.3 新增】对卖家完整行为逻辑链的最终总结。必须明确回答：这是一个怎样的卖家？其买卖行为是否构成一个可信的个人故事？",

          "evidence": "例如：'该卖家的行为逻辑链完整：早期购买游戏，中期购入相机和镜头，近期开始出售旧款电子设备。这是一个典型的数码产品消费者的成长路径，可信度极高。' 或 '逻辑链断裂：该卖家大量购买维修配件，却声称所有售卖设备均为自用，故事不可信。'"

        }

      }

    },

    "shipping": { "status": "string", "comment": "string", "evidence": "string" },

    "seller_credit": { "status": "string", "comment": "string", "evidence": "string" }

  }

}

"""

    # [--- END OF MODIFICATION ---]



    # 1. 将 system prompt 和 user prompt 的文本内容合并

    combined_text_prompt = f"""{system_prompt}



请基于你的专业知识和我的要求，分析以下完整的商品JSON数据：



```json

{product_details_json}

```

"""



    # 2. 构建一个内容列表，包含合并后的文本和所有图片

    user_content_list = [{"type": "text", "text": combined_text_prompt}]



    if image_paths:

        for path in image_paths:

            base64_image = encode_image_to_base64(path)

            if base64_image:

                user_content_list.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}})



    # 3. 构建最终的 messages 列表，只包含一个 user role

    messages = [{"role": "user", "content": user_content_list}]



    response = client.chat.completions.create(

        model=MODEL_NAME,

        messages=messages,

        max_tokens=999999, # 调整Token以适应更长的上下文

        response_format={"type": "json_object"}

    )

    

    ai_response_content = response.choices[0].message.content



    try:

        return json.loads(ai_response_content)

    except json.JSONDecodeError as e:

        print("---!!! AI RESPONSE PARSING FAILED (JSONDecodeError) !!!---")

        print("这通常意味着AI模型没有返回一个有效的JSON对象，可能是因为响应被截断或模型未遵循指令。")

        print(f"原始返回值 (Raw response from AI):\n---\n{ai_response_content}\n---")

        # 向上抛出异常，让 @retry_on_failure 装饰器能够捕获并重试

        raise e



def main():

    """主函数，执行整个流程。"""

    parser = argparse.ArgumentParser(description="AI商品筛选器")

    parser.add_argument("keyword", type=str, help="要搜索的关键词（必须）")

    parser.add_argument('--auto-mode', action='store_true', help='开启自动模式，发现新推荐商品时立即发送ntfy.sh通知。')

    args = parser.parse_args()



    # 新增：加载已有的分析结果

    try:

        with open(RESULTS_FILE, 'r', encoding='utf-8') as f:

            existing_results = json.load(f)

        print(f"成功加载 {len(existing_results)} 条已有的分析结果。")

    except (FileNotFoundError, json.JSONDecodeError):

        existing_results = {}

        print("未找到或无法解析结果文件，将从头开始分析。")



    products = parse_product_file(f"{args.keyword.replace(' ', '_')}_full_data.jsonl")

    if not products:

        print("未能解析到任何商品信息，程序退出。")

        return



    for product in products:

        # 从 product['item_info'] 中获取信息

        item_info = product.get('商品信息', {})

        product_id = item_info.get('商品ID')

        

        if not product_id:

            print(f"警告：发现一个没有商品ID的记录，已跳过。数据: {item_info.get('商品标题', 'N/A')}")

            continue



        # 检查商品是否已经被处理过

        if product_id in existing_results:

            print(f"商品 #{product_id} 已在结果文件中找到，跳过。")

            print("-" * 40)

            continue

            

        # --- 如果是新商品，则继续执行 ---

        image_urls = item_info.get('商品图片列表') # 从 item_info 获取

        

        local_image_paths = download_all_images(product_id, image_urls)

        

        # 传递整个 product 对象给AI分析

        ai_result = get_ai_analysis(product, local_image_paths)

        

        if ai_result is None:

            print(f"商品 #{product_id} AI分析失败，跳过。")

            print("=" * 40)

            continue



        # --- START: 新增的通知模块 ---

        if args.auto_mode and ai_result.get('is_recommended'):

            print(">>> 自动模式: 发现新推荐商品，准备发送通知...")

            send_ntfy_notification(item_info, ai_result.get('reason'))

        # --- END: 新增的通知模块 ---

            

        print("\n--- AI 分析结果 ---")

        pprint(ai_result)

        

        # 构建当前商品的结果

        current_result = {

            "product_id": product_id,

            "title": item_info.get("商品标题"),

            "price": item_info.get("当前售价"),

            "link": item_info.get("商品链接"),

            "seller_profile": product.get("卖家信息", {}), # <-- 新增此行

            "analysis": ai_result

        }

        

        # 优化：创建一个只包含基本卖家信息的 "seller_summary" 字典

        seller_profile_data = current_result.get("seller_profile", {})

        seller_summary = {

            "卖家昵称": seller_profile_data.get("卖家昵称", "N/A"),

            "卖家信用等级": seller_profile_data.get("卖家信用等级", "N/A"),

            "卖家收到的评价总数": seller_profile_data.get("卖家收到的评价总数", "N/A"),

            "卖家在售/已售商品数": seller_profile_data.get("卖家在售/已售商品数", "N/A"),

            "卖家注册时长": seller_profile_data.get("卖家注册时长", "N/A")

        }

        

        # 将新创建的摘要添加到结果中，并删除完整但冗余的原始信息

        current_result['seller_summary'] = seller_summary

        if 'seller_profile' in current_result:

            del current_result['seller_profile']

        

        # 更新结果字典并立即保存到文件

        existing_results[product_id] = current_result

        with open(RESULTS_FILE, 'w', encoding='utf-8') as f:

            json.dump(existing_results, f, ensure_ascii=False, indent=4)

        print(f"已将商品 #{product_id} 的分析结果保存到 {RESULTS_FILE}")

        print("=" * 40)

    

    print("\n\n===== ✅ 分析完毕，完整推荐列表如下 =====")

    recommended_count = 0

    # 新增：从包含所有历史记录的 'existing_results' 中生成报告

    for result in existing_results.values():

        analysis = result.get('analysis', {})

        if analysis and analysis.get('is_recommended'):

            recommended_count += 1

            # 从新的 seller_summary 结构中提取卖家信息

            seller_summary = result.get('seller_summary', {})

            seller_name = seller_summary.get('卖家昵称', 'N/A')

            seller_credit = seller_summary.get('卖家信用等级', 'N/A')

            seller_reviews = seller_summary.get('卖家收到的评价总数', 'N/A')

            seller_items_count = seller_summary.get('卖家在售/已售商品数', 'N/A')

            day = seller_summary.get('卖家注册时长', 'N/A')

            print(f"【推荐】商品ID: {result['product_id']} | 价格: {result['price']}")

            print(f"  卖家: {seller_name} (信用: {seller_credit} | 评价数: {seller_reviews} | 交易数: {seller_items_count}) | 注册时长: {day}")

            print(f"  标题: {result['title']}")

            print(f"  原因: {analysis.get('reason')}")

            print(f"  链接: {result['link']}\n")

    

    if recommended_count == 0:

        print("在所有已分析的商品中，没有找到符合所有条件的推荐商品。")




if __name__ == "__main__":

    main()