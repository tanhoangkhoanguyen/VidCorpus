from selenium import webdriver
from selenium.webdriver.chrome.options import Options


class ChromeDriver:
    def __init__(self, window_size: str = None):
        options = Options()
        options.add_argument("--disable-blink-features=AutomationControlled")
        # One call - a second add_experimental_option with the same key overwrites the first.
        options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        options.add_experimental_option("useAutomationExtension", False)
        if window_size:
            options.add_argument(f"--window-size={window_size}")
        self.driver = webdriver.Chrome(options = options)

    def get_driver(self):
        return self.driver

    def close(self):
        self.driver.quit()
