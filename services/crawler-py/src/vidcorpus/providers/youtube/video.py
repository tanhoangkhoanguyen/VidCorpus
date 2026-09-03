from vidcorpus.core.browser import ChromeDriver
from vidcorpus.schema import VideoSchema, SingleCommentSchema, CommentSchema
from vidcorpus.core.log import get_logger

import os, requests, time, yt_dlp, json, warnings
warnings.filterwarnings("ignore")
from selenium.webdriver.common.by import By
from concurrent.futures import ThreadPoolExecutor

LOGGER = get_logger(__name__)

class YoutubeVideoCrawler:
    def __init__(self):
        self.url = None
        self.storage_path = None
        self.metadata = VideoSchema()
        self.driver = ChromeDriver(window_size="--window-size=300,1000").get_driver()

    def check_link(self):
        response_check = requests.get(self.url, allow_redirects=True)
        final_url = response_check.url
        if not final_url.startswith("https://www.youtube.com"):
            LOGGER.warning("Wrong input link")
            return False
        self.url = final_url
        return True

    def save_to_json(self):
        filename = f"youtube_{self.metadata.id}.json"
        file_path = os.path.join(self.storage_path, filename)
        try:
            with open(file_path, 'w', encoding = 'utf-8') as f:
                json.dump(self.metadata.model_dump(), f, ensure_ascii = False, indent = 4)
            LOGGER.info(f"Saved data for video {self.metadata.id}")
        except Exception as e:
            LOGGER.error(f"Unable to save data for video {self.metadata.id}:\n\t{str(e)}")

    def crawl_metadata(self):
        ydl_opts = {
            "quiet": True,
            "skip_download": True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(self.url, download=False)
        self.metadata = VideoSchema(
            id          = info.get("id"),
            title       = info.get("title"),
            channel     = info.get("channel"),
            channel_id  = info.get("channel_id"),
            subscribers = info.get("channel_follower_count"),
            description = info.get("description"),
            date        = info.get("upload_date"),
            views       = info.get("view_count"),
            likes       = info.get("like_count")
        )

    def crawl_comments(
            self, 
            scrolling: int = 1
        ):
        self.driver.get(self.url)
        time.sleep(2)

        for _ in range(scrolling):
            self.driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight);")
            time.sleep(2)

        while True:
            try:
                buttons = self.driver.find_elements(
                    By.XPATH, 
                    '//ytd-button-renderer//a[contains(@aria-label,"reply")]'
                )
                if not buttons:
                    break
                for b in buttons:
                    self.driver.execute_script("arguments[0].click();", b)
                    time.sleep(2)
            except:
                break

        threads = self.driver.find_elements(
            By.CSS_SELECTOR, 
            "ytd-comment-thread-renderer"
        )
        for e in threads:
            try:
                author = e.find_element(
                    By.CSS_SELECTOR, 
                    "#author-text"
                ).text
                text = e.find_element(
                    By.CSS_SELECTOR, 
                    "#content-text"
                ).text
                comment_data = CommentSchema(
                    author = author, 
                    comment = text, 
                    replies = []
                )

                replies = e.find_elements(By.CSS_SELECTOR, "ytd-comment-renderer #content-text")
                for r in replies[1:]:
                    try:
                        reply_author = r.find_element(
                            By.XPATH, 
                            "../../..//a[@id='author-text']"
                        ).text
                        reply_text = r.text
                        comment_data.replies.append(
                            SingleCommentSchema(
                                author = reply_author, 
                                comment = reply_text
                            ))
                    except:
                        continue
                self.metadata.comments.append(comment_data)
            except:
                continue

    def download_video(self):
        try:
            filename_template = f"youtube_{self.metadata.id}.%(ext)s"
            file_path = os.path.join(self.storage_path, filename_template)
            ydl_opts = {
                "outtmpl": file_path,
                "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]",
                "merge_output_format": "mp4",
                "quiet": True
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.url])
            LOGGER.info(f"Saved video {self.metadata.id}")
        except Exception as e:
            LOGGER.error(f"Unable to download video {self.metadata.id}\n\t{str(e)}")

    def quit_driver(self):
        self.driver.quit()

    def run(
            self, 
            url: str
        ):
        self.url = url
        if not self.check_link():
            return

        self.crawl_metadata()
        self.storage_path = os.path.join("data", self.metadata.id)
        os.makedirs(self.storage_path, exist_ok = True)

        with ThreadPoolExecutor(max_workers = 2) as executor:
            executor.submit(self.crawl_comments)
            executor.submit(self.download_video)

        self.save_to_json()