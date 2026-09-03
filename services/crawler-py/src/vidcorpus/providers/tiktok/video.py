from vidcorpus.core.browser import ChromeDriver
from vidcorpus.schema import VideoSchema, SingleCommentSchema, CommentSchema
from vidcorpus.core.log import get_logger

import json, requests, time, re, os, warnings
warnings.filterwarnings("ignore")
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup

LOGGER = get_logger(__name__)
TIKTOK_HEADERS = {
    "accept": "*/*",
    "accept-language": "vi,en;q=0.9,en-GB;q=0.8,en-US;q=0.7",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
    "Referer": "https://www.tiktok.com/",
    "Accept-Encoding": "identity;q=1, *;q=0"
}
TIKTOK_COOKIES = {
    'tt_webid': '1' * 19,
    'tt_webid_v2': '1' * 19
}

class TikTokVideoCrawler:
    def __init__(self):
        self.url = None
        self.storage_path = None
        self.video_download_url = None
        self.metadata = VideoSchema()
        self.session = requests.session()
        self.driver = ChromeDriver(window_size = "--window-size=300,1000").get_driver()

    def __debug_session(self, resp):
        if not resp.ok:
            LOGGER.error(f"response body: {resp.text}")
        resp.raise_for_status()

    def check_url(self):
        response_check = requests.get(self.url, allow_redirects = True)
        final_url = response_check.url
        if not final_url.startswith((
                "https://vt.tiktok.com", 
                "https://www.tiktok.com"
            )):
            LOGGER.warning("Wrong input url")
            return False
        self.url = final_url
        return True

    def save_to_json(self):
        filename = f"tiktok_{self.metadata.id}.json"
        file_path = os.path.join(self.storage_path, filename)
        try:
            with open(file_path, 'w', encoding = 'utf-8') as f:
                json.dump(self.metadata.model_dump(), f, ensure_ascii = False, indent = 4)
            LOGGER.info(f"Saved data for video {self.metadata.id}")
        except Exception as e:
            LOGGER.error(f"Unable to save data for video {self.metadata.id}:\n\t{str(e)}")

    def crawl_metadata(self):
        response = self.session.get(
            self.url, 
            headers = TIKTOK_HEADERS, 
            cookies = TIKTOK_COOKIES
        )
        self.metadata.id = re.search(r'/video/(\d+)', self.url).group(1)
        soup = BeautifulSoup(response.text, "html.parser")
        script_tag = soup.find("script", id = "__UNIVERSAL_DATA_FOR_REHYDRATION__")
        if script_tag:
            try:
                raw_json = script_tag.string
                data = json.loads(raw_json)
                item_struct = data['__DEFAULT_SCOPE__']['webapp.video-detail']['itemInfo']['itemStruct']
                text_tags = item_struct.get('textExtra', [])
                self.metadata.channel      = re.search(r'tiktok\.com/@([^/]+)/video', self.url).group(1)
                self.metadata.description  = re.sub(r'#\w+', '', item_struct.get('desc', '')).strip()
                self.metadata.date         = item_struct['createTime']
                self.metadata.likes        = int(item_struct.get('stats', {}).get('diggCount', ''))
                self.metadata.views        = int(item_struct.get('stats', {}).get('playCount', ''))
                self.metadata.hashtag      = ['#'+tag.get('hashtagName', '') for tag in text_tags if tag.get('hashtagName')]
                self.video_download_url = item_struct.get('video', {}).get('playAddr', '')
            except Exception as e:
                LOGGER.error(f"Unable to extract info from video {self.metadata.id}:\n\t{str(e)}")
    
    def crawl_comments(
            self,
            scolling: int = 1,
            view_relies: int = 1
        ):
        self.driver.get(self.url)
        time.sleep(2)

        comment_btn = WebDriverWait(self.driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-e2e='comment-icon']"))
        )
        comment_btn.click()

        for _ in range (scolling):
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
        
        for _ in range (view_relies):
            try:
                view_buttons = self.driver.find_elements(
                    By.XPATH,
                    "//div[contains(@class,'DivViewRepliesContainer')]"
                )
                for btn in view_buttons:
                    try:
                        text = btn.text.strip()
                        if "View" not in text:
                            continue
                        self.driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                        btn.click()
                        time.sleep(0.2)
                    except:
                        continue
            except:
                continue

        raw_html = self.driver.page_source
        soup = BeautifulSoup(raw_html, "html.parser")
        comment_wrappers = soup.select('div[class*="DivCommentObjectWrapper"]')
        for wrapper in comment_wrappers:
            try:
                level1 = wrapper.select_one('div[class*="DivCommentItemWrapper"]')
                if not level1:
                    continue

                author_tag_1 = level1.select_one('div[data-e2e="comment-username-1"] a p')
                author_1 = author_tag_1.get_text(strip=True) if author_tag_1 else ""

                comment_tag_1 = level1.select_one('span[data-e2e="comment-level-1"]')
                comment_text_1 = comment_tag_1.get_text(strip=True) if comment_tag_1 else ""

                comment_data = CommentSchema(
                    author=author_1,
                    comment=comment_text_1,
                    replies=[]
                )

                reply_container = wrapper.select_one('div[class*="DivReplyContainer"]')
                if reply_container:
                    reply_wrappers = reply_container.select('div[class*="DivCommentItemWrapper"]')
                    for reply in reply_wrappers:
                        author_tag_2 = reply.select_one('div[data-e2e="comment-username-2"] a p')
                        author_2 = author_tag_2.get_text(strip=True) if author_tag_2 else ""

                        comment_tag_2 = reply.select_one('span[data-e2e="comment-level-2"]')
                        if comment_tag_2:
                            direct_spans = comment_tag_2.find_all('span', recursive=False)
                            if direct_spans:
                                comment_text_2 = " ".join(
                                    s.get_text(strip=True) for s in direct_spans
                                    if s.get_text(strip=True)
                                )
                            else:
                                comment_text_2 = comment_tag_2.get_text(strip=True)
                        else:
                            comment_text_2 = ""

                        if author_2 and comment_text_2:
                            comment_data.replies.append(SingleCommentSchema(
                                author=author_2,
                                comment=comment_text_2
                            ))

                self.metadata.comments.append(comment_data)
            except Exception as e:
                print(f"Error parsing comment: {e}")
                continue

    def download_video(self):
        if not self.video_download_url:
            LOGGER.error(f"Unable to download video {self.metadata.id}")
            return

        filename = f"tiktok_{self.metadata.id}.mp4"
        file_path = os.path.join(self.storage_path, filename)
        headers = TIKTOK_HEADERS
        headers["Range"] = "bytes=0-"
        
        try:
            resp = self.session.get(self.video_download_url, headers = headers, cookies = TIKTOK_COOKIES)
            self.__debug_session(resp)
            with open(file_path, 'wb') as f:
                f.write(resp.content)
            LOGGER.info(f"Saved video {self.metadata.id}")
        except Exception as e:
            LOGGER.error(f"Undefined error for video {self.metadata.id}:\n\t{str(e)}")

    def quit_driver(self):
        self.driver.quit()
        
    def run(self, url: str):
        self.url = url
        if self.check_url() == False:
            return

        self.crawl_metadata()
        self.storage_path = os.path.join("data/", self.metadata.id)
        os.makedirs(self.storage_path, exist_ok = True)

        with ThreadPoolExecutor(max_workers = 2) as executor:
            executor.submit(self.crawl_comments)
            executor.submit(self.download_video)

        self.save_to_json()