from selenium import webdriver
from selenium.webdriver.chrome.options import Options

class ChromeDriver:
    def __init__(self, window_size = None):
        chrome_options = Options()
        # Prevents from being detected automation
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)
        chrome_options.add_experimental_option("excludeSwitches", ["enable-logging"])
        if window_size:
            chrome_options.add_argument(f"--window-size={window_size}")
        self.driver = webdriver.Chrome(options = chrome_options)
    
    def get_driver(self):
        return self.driver
    
    def quit_driver(self):
        self.driver.quit()