from flask import Flask, request, jsonify
import json
import os
from scraper import run_scraper

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, '..', 'static')
DATA_DIR = os.path.join(BASE_DIR, '..', 'data')

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='')

@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/api/data', methods=['GET'])
def get_data():
    filename = os.path.join(DATA_DIR, "data.json")
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return jsonify(data)
        except Exception:
            return jsonify([])
    return jsonify([])

@app.route('/api/scrape', methods=['POST'])
def scrape():
    data = request.json
    query = data.get('query')
    
    if not query:
        return jsonify({"error": "Query is required"}), 400
        
    print(f"Received scrape request for: {query}")
    
    # Run the scraper
    results = run_scraper(query)
    
    # Load existing data to append/upsert
    filename = os.path.join(DATA_DIR, "data.json")
    existing_data = []
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
        except Exception:
            pass
            
    # Upsert logic based on Website or Company Name
    existing_dict = {item.get('Website', item.get('Company_Name')): idx for idx, item in enumerate(existing_data)}
    
    for item in results:
        key = item.get('Website')
        if key == 'N/A' or not key:
            key = item.get('Company_Name')
            
        if key in existing_dict:
            existing_data[existing_dict[key]] = item
        else:
            existing_data.append(item)
            existing_dict[key] = len(existing_data) - 1
            
    # Save back to json
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(existing_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print("Error saving data:", e)
        
    # Return ONLY the new results for this search (as requested)
    return jsonify({
        "message": "Scraping completed", 
        "new_results": len(results),
        "total_results": len(existing_data),
        "data": results
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
