from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import requests
import time
import mysql.connector

db_connection = mysql.connector.connect(
    host="localhost",
    user="root",
    password="",
    database="gbl"
)



chrome_options=Options()
chrome_options.add_argument('--headless')
chrome_options.add_argument('--no-sandbox')

cur = db_connection.cursor()

def create_table():
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS comp_datails (
                Company_Name VARCHAR(255),
                Address VARCHAR(255),
                Website VARCHAR(255) PRIMARY KEY,
                Phone_Number VARCHAR(20),
                review_Average FLOAT,
                review_count INT
            )
        """)
        db_connection.commit()
        print("Table 'comp_datails' created or already exists.")
    except Exception as e:
        print("Error creating table:", e)


create_table()


driver = webdriver.Chrome(options=chrome_options)

def scrap_data(query):
    driver.get("https://www.google.com/maps")
    time.sleep(3)

    search_box = driver.find_element("name", "q")
    search_box.send_keys(query)
    search_box.send_keys(Keys.RETURN)
    time.sleep(5)

    html = driver.page_source
    soup = BeautifulSoup(html, 'html.parser')

    l1 = []
    all_links = soup.find_all('a', class_="hfpxzc")

    for link in all_links:
        l1.append(link.get('href'))

    for link in l1:
        driver.get(link)
        htmlcontent = driver.page_source
        soup = BeautifulSoup(htmlcontent, 'html.parser') 

        try:
            Company_name = soup.find_all('h1')[0].text
            Company_address = soup.find_all('div', class_="Io6YTe fontBodyMedium kR99db")[0].text
            Comapny_web = soup.find_all('div', class_="Io6YTe fontBodyMedium kR99db")[1].text
            Comapnay_phone = soup.find_all('div', class_="Io6YTe fontBodyMedium kR99db")[2].text.strip()  # strip to remove any extra whitespace

            # Validate Phone Number (only numeric characters allowed)
            if not Comapnay_phone.isdigit():
                Comapnay_phone = ''

            rating_fetch = soup.find_all('div', class_="F7nice")
            
            for rating_info in rating_fetch:   
                try:
                    rating = float(rating_info.text.split("(")[0])
                    people_rated = int(rating_info.text.split("(")[1].split(")")[0])
                except ValueError:
                    rating = 0.0
                    people_rated = 0

        except Exception as e:
            print("Error scraping data:", e)
            continue

        try:
            insert_query = """
                INSERT INTO comp_datails (Company_Name, Address, Website, Phone_Number, review_Average, review_count)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                Company_Name = VALUES(Company_Name),
                Address = VALUES(Address),
                Website = VALUES(Website),
                Phone_Number = VALUES(Phone_Number),
                review_Average = VALUES(review_Average),
                review_count = VALUES(review_count)
            """
            insert_data = (Company_name, Company_address, Comapny_web, Comapnay_phone, rating, people_rated)
            cur.execute(insert_query, insert_data)
            db_connection.commit()
        except Exception as e:
            print("Error inserting data:", e)

       

# Call the function to scrape data
scrap_data("It companies")

# Close the WebDriver
driver.quit()
