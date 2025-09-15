from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import mysql.connector
from datetime import datetime
import re
import google.generativeai as genai
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.units import inch
import smtplib
from email.message import EmailMessage
import tempfile
import os

# === API KEYS & CONFIG ===
GEMINI_API_KEY = "AIzaSyDd1snCP08oE20AnfKbHP4VzBob4ac-i6g"
#GEMINI_API_KEY="AIzaSyBFOHK4MZDcb59oMyEIzaAiNLulz9CvQro"
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'Chai13062004@'
}
DB_NAME = "interviewy_db"

# === Gmail SMTP Config ===
GMAIL_SMTP = "smtp.gmail.com"
GMAIL_PORT = 587
GMAIL_SENDER = "chaitanyathakre13@gmail.com"
GMAIL_PASSWORD = "pflc yidj osnh aiiw"

# Set static_folder to 'static' (C:\InterviewyApp\static)
app = Flask(__name__, static_folder='static', template_folder='static')
CORS(app)

genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel('gemini-1.5-flash-latest')

def db_setup():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}")
    conn.commit()
    conn.close()
    conn = mysql.connector.connect(database=DB_NAME, **DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS interviews (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255),
            email VARCHAR(255),
            major VARCHAR(255),
            degree_level VARCHAR(255),
            difficulty VARCHAR(50),
            score DOUBLE,
            total_questions INT,
            datetime DATETIME
        ) ENGINE=InnoDB;
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS questions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            interview_id INT,
            question_text TEXT,
            answer TEXT,
            user_answer TEXT,
            points DOUBLE,
            FOREIGN KEY(interview_id) REFERENCES interviews(id)
        ) ENGINE=InnoDB;
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chatlog (
            id INT AUTO_INCREMENT PRIMARY KEY,
            interview_id INT,
            sender VARCHAR(255),
            text TEXT,
            is_user TINYINT,
            timestamp DATETIME,
            FOREIGN KEY(interview_id) REFERENCES interviews(id)
        ) ENGINE=InnoDB;
    ''')
    conn.commit()
    cursor.close()
    conn.close()
db_setup()

def normalize_major(major):
    major = major.lower().strip()
    mapping = {
        'cs': 'cs', 'computer science': 'cs', 'computer_science': 'cs',
        'se': 'software_engineer', 'software engineering': 'software_engineer',
        'software engineer': 'software_engineer', 'software_engineer': 'software_engineer',
        'data science': 'data_scientist', 'data scientist': 'data_scientist',
        'ai': 'artificial_intelligence', 'artificial intelligence': 'artificial_intelligence',
        'cyber security': 'cyber_security', 'cybersecurity': 'cyber_security',
        'bba': 'bba', 'business administration': 'bba',
        'product management': 'product_management'
    }
    return mapping.get(major, major)

def degree_template(degree_level):
    if degree_level in ['bachelors', 'undergraduate', 'bachelor', 'bsc']:
        return ("Beginner to intermediate concepts. Questions should be mostly factual, conceptual, or basic practical, "
                "with short to moderate answers (2-4 sentences).")
    elif degree_level in ['masters', 'graduate', 'msc', 'ms']:
        return ("Intermediate to advanced concepts. Questions may include applied, theoretical, or design aspects. "
                "Answers should be moderately detailed (4-8 sentences), with some explanation or examples.")
    elif degree_level in ['mphil', 'phd', 'doctorate']:
        return ("Advanced to expert-level concepts. Questions should be analytical, research-oriented, or open-ended, "
                "sometimes requiring critical thinking, synthesis, or evaluation. Answers must be detailed and comprehensive (8+ sentences or more if needed).")
    else:
        return ("General technical concepts appropriate to the specified degree level.")

def difficulty_template(difficulty):
    if difficulty == "easy":
        return "Focus on fundamental or entry-level topics. Avoid trick questions or advanced material."
    elif difficulty == "medium":
        return "Include some challenging topics and require understanding, not just recall."
    elif difficulty == "hard":
        return "Require deep technical knowledge, critical thinking, and synthesis. Answers may involve multiple steps, explanations, or examples."
    else:
        return "Use an appropriate general difficulty."
    
def handle_off_topic_answer(question, user_answer, correct_answer):
    """
    Detects if the user's answer is off-topic (like asking a question instead of answering).
    If so, the bot will answer their off-topic query and then bring them back to the interview.
    """
    # Check: if user answer looks like a question (off-topic)
    if user_answer.strip().endswith("?") or user_answer.lower().startswith(("what", "why", "how", "when", "where")):
        try:
            # Use Gemini to answer the off-topic query
            prompt = (
                f"The candidate asked an off-topic question during an interview.\n"
                f"Off-topic Question: {user_answer}\n\n"
                "Answer their question briefly and then politely bring them back to the original interview question:\n"
                f"'{question}'"
            )
            response = gemini_model.generate_content(prompt)
            reply_text = response.text.strip()
            return {
                "is_off_topic": True,
                "reply": reply_text
            }
        except Exception as e:
            print("Off-topic handling error:", e)
            return {"is_off_topic": False}
    return {"is_off_topic": False}


def fetch_questions_gemini(major, degree_level, difficulty):
    deg_temp = degree_template(degree_level)
    diff_temp = difficulty_template(difficulty)
    prompt = (
        f"You are an expert interviewer for the field of {major}. "
        f"Generate 10 technical interview questions strictly about {major}, "
        f"for a candidate with {degree_level} ({difficulty}) level. "
        f"{deg_temp} {diff_temp}\n\n"
        "For each question, provide a detailed, correct model answer. "
        "Format output strictly as:\n"
        "Q1: ...\nA1: ...\nQ2: ...\nA2: ...\nQ3: ...\nA3: ...\nQ4: ...\nA4: ...\nQ5: ...\nA5: ...\n"
        "Q6: ...\nA6: ...\nQ7: ...\nA7: ...\nQ8: ...\nA8: ...\nQ9: ...\nA9: ...\nQ10: ...\nA10: ...\n"
        "Do not generate questions outside of the field {major}. Do not add explanations, greetings, or extra text. "
        "The questions must fit the degree and difficulty level closely, and the answers must be longer and more technical for harder degrees/difficulties."
    )
    try:
        response = gemini_model.generate_content(prompt)
        text = response.text
        questions = parse_questions_answers(text)
        filtered = []
        for qa in questions:
            if len(qa['question_text']) < 8:
                continue
            filtered.append(qa)
        if len(filtered) < 7:
            print("Gemini output unsatisfactory, retrying with stricter prompt...")
        return filtered[:10]
    except Exception as e:
        print(f"Error fetching questions from Gemini: {e}")
        return []

def parse_questions_answers(text):
    qa_pairs = []
    pattern = re.compile(r"Q\d+:\s*(.+?)\s*A\d+:\s*(.+?)(?=(?:Q\d+:|$))", re.DOTALL)
    for match in pattern.finditer(text):
        question = match.group(1).strip()
        answer = match.group(2).strip()
        qa_pairs.append({"question_text": question, "answer": answer})
    return qa_pairs

def gemini_smart_score(question, user_answer, model_answer):
    prompt = (
        f"You are a technical interview grader.\n"
        f"Question: {question}\n"
        f"Model Answer: {model_answer}\n"
        f"Candidate's Answer: {user_answer}\n"
        "Give a score between 0.0 (completely incorrect) to 1.0 (perfect answer), and a one-sentence feedback. "
        "Use the following JSON format:\n"
        '{"score": float, "feedback": "string"}\n'
        "Be fair: if the user gives a correct but concise answer, give full marks."
    )
    try:
        response = gemini_model.generate_content(prompt)
        match = re.search(r'{"score":\s*([0-9.]+),\s*"feedback":\s*"([^"]+)"}', response.text)
        if match:
            score = float(match.group(1))
            feedback = match.group(2)
            return score, feedback
    except Exception as e:
        print("Gemini grading error:", e)
    # fallback to word overlap
    return simple_word_overlap(user_answer, model_answer), "No AI feedback."

def simple_word_overlap(user_answer, model_answer):
    awords = re.sub(r'[^\w\s]', '', user_answer.strip().lower()).split()
    bwords = re.sub(r'[^\w\s]', '', model_answer.strip().lower()).split()
    if not bwords or not awords:
        return 0.0
    match = sum(1 for w in awords if w in bwords)
    percent = match / len(bwords)
    return percent

def generate_gemini_feedback_and_resources(interview, questions):
    feedback_prompt = (
        f"You are an expert interview coach. Here is an interview summary:\n"
        f"Major: {interview['major']}\n"
        f"Degree: {interview['degree_level']}\n"
        f"Difficulty: {interview['difficulty']}\n"
        f"Score: {interview['score']} / {interview['total_questions']}\n"
        "Questions and answers:\n" +
        "\n".join([
            f"Q{i+1}: {q['question_text']}\nUser Answer: {q['user_answer']}\nModel Answer: {q['answer']}\nPoints: {q['points']}"
            for i, q in enumerate(questions)
        ]) +
        "\n\nAnalyze the answers and performance. Give a concise personalized feedback for this candidate, mentioning their strengths and the most critical weaknesses based on their answers. Limit to 3 lines."
    )

    weak_topics = [q['question_text'] for q in questions if q['points'] is not None and q['points'] < 0.7]
    if not weak_topics:
        weak_topics = [q['question_text'] for q in questions[:2]]
    topic_str = "; ".join(weak_topics)
    recom_prompt = (
        f"You are a helpful AI for interview preparation. The candidate needs to improve in these areas: {topic_str}.\n"
        f"For the field of {interview['major']}, recommend 3 to 5 high-quality, up-to-date resources most relevant to these topics. "
        f"Include direct clickable links (YouTube videos, websites, or documentation). List as markdown bullets with a 1-line description. "
        f"Each bullet: [Title](url): description. Ensure all links work and are highly relevant to the candidate's weaknesses."
    )
    feedback = ""
    resources = []
    try:
        resp = gemini_model.generate_content([{"role": "user", "parts": [feedback_prompt]}])
        feedback = resp.text.strip()
    except Exception as e:
        feedback = "Could not generate feedback. Please review the model answers and keep practicing."
    try:
        resp = gemini_model.generate_content([{"role": "user", "parts": [recom_prompt]}])
        markdown = resp.text.strip()
        resources = []
        for line in markdown.splitlines():
            m = re.match(r"- \[(.+?)\]\((https?://[^\)]+)\):\s*(.+)", line)
            if m:
                resources.append({"title": m.group(1), "url": m.group(2), "desc": m.group(3)})
        if not resources:
            for line in markdown.splitlines():
                m = re.search(r"\[(.+?)\]\((https?://[^\)]+)\)", line)
                if m:
                    resources.append({"title": m.group(1), "url": m.group(2), "desc": ""})
    except Exception as e:
        resources = []
    return feedback, resources

# Route for root: serves index.html from static folder
@app.route('/')
def serve_index():
    return send_from_directory(app.static_folder, 'index.html')

# Route for other html pages: home.html, int.html, result.html, ok.html
@app.route('/<page>')
def serve_page(page):
    if page.endswith('.html'):
        return send_from_directory(app.static_folder, page)
    return send_from_directory(app.static_folder, page)

# Route for static files (css/js/images)
@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

# (Optional) For React/Vue SPA fallback for client-side routing, uncomment:
# @app.route('/<path:path>')
# def catch_all(path):
#     if '.' in path:  # for static files
#         return send_from_directory(app.static_folder, path)
#     return send_from_directory(app.static_folder, 'index.html')

@app.route('/api/start_interview', methods=['POST'])
def start_interview():
    data = request.json
    name = data.get("name", "").strip()
    email = data.get("email", "").strip()
    major = normalize_major(data.get("major", "").strip())
    degree_level = data.get("degree_level", "").strip().lower()
    difficulty = data.get("difficulty", "").strip().lower()
    if not (name and email and major and degree_level and difficulty):
        return jsonify({"error": "Missing required fields"}), 400
    questions = fetch_questions_gemini(major, degree_level, difficulty)
    if not questions:
        return jsonify({"error": "Could not generate questions"}), 500
    conn = mysql.connector.connect(database=DB_NAME, **DB_CONFIG)
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT INTO interviews (name, email, major, degree_level, difficulty, score, total_questions, datetime) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
              (name, email, major, degree_level, difficulty, 0.0, len(questions), now))
    conn.commit()
    interview_id = cursor.lastrowid
    for q in questions:
        cursor.execute("INSERT INTO questions (interview_id, question_text, answer, user_answer, points) VALUES (%s, %s, %s, %s, %s)",
                  (interview_id, q['question_text'], q['answer'], '', 0.0))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({
        "interview_id": interview_id,
        "questions": [{"id": idx, "question_text": q["question_text"]} for idx, q in enumerate(questions)]
    })

@app.route('/api/answer_question', methods=['POST'])
def answer_question():
    data = request.json
    interview_id = data.get("interview_id")
    question_index = data.get("question_index")
    answer = data.get("answer", "")
    if interview_id is None or question_index is None:
        return jsonify({"error": "Missing interview_id or question_index"}), 400
    conn = mysql.connector.connect(database=DB_NAME, **DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM questions WHERE interview_id=%s ORDER BY id LIMIT %s,1", (interview_id, question_index))
    question = cursor.fetchone()
    if not question:
        cursor.close()
        conn.close()
        return jsonify({"error": "Question not found"}), 404
    correct_answer = question['answer']

        # === NEW FEATURE: Handle off-topic answers ===
    off_topic_result = handle_off_topic_answer(question['question_text'], answer, correct_answer)
    if off_topic_result["is_off_topic"]:
        cursor.close()
        conn.close()
        return jsonify({
            "points": 0.0,
            "feedback": off_topic_result["reply"],
            "correct_answer": correct_answer,
            "total_score": total_score if 'total_score' in locals() else 0
        })


    points, feedback = gemini_smart_score(question['question_text'], answer, correct_answer)
    points = max(0, min(1.0, round(points, 2)))  # Ensure within [0, 1]

    cursor.execute("UPDATE questions SET user_answer=%s, points=%s WHERE id=%s", (answer, points, question['id']))
    conn.commit()
    cursor.execute("SELECT SUM(points) AS total_score FROM questions WHERE interview_id=%s", (interview_id,))
    total_score = cursor.fetchone()["total_score"] or 0
    cursor.execute("UPDATE interviews SET score=%s WHERE id=%s", (total_score, interview_id))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({
        "points": points,
        "feedback": feedback,
        "correct_answer": correct_answer,
        "total_score": total_score
    })

@app.route('/api/skip_question', methods=['POST'])
def skip_question():
    data = request.json
    interview_id = data.get("interview_id")
    question_index = data.get("question_index")
    if interview_id is None or question_index is None:
        return jsonify({"error": "Missing interview_id or question_index"}), 400
    conn = mysql.connector.connect(database=DB_NAME, **DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM questions WHERE interview_id=%s ORDER BY id LIMIT %s,1", (interview_id, question_index))
    question = cursor.fetchone()
    if not question:
        cursor.close()
        conn.close()
        return jsonify({"error": "Question not found"}), 404
    correct_answer = question['answer']
    cursor.execute("UPDATE questions SET user_answer=%s, points=%s WHERE id=%s", ('', 0.0, question['id']))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({
        "correct_answer": correct_answer
    })

@app.route('/api/save_interview_history', methods=['POST'])
def save_interview_history():
    data = request.json
    interview_id = data.get("interview_id")
    history = data.get("history", [])
    cheated = data.get("cheated", False)
    if not interview_id or not isinstance(history, list):
        return jsonify({"error": "Missing or invalid parameters"}), 400
    conn = mysql.connector.connect(database=DB_NAME, **DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chatlog WHERE interview_id = %s", (interview_id,))
    for msg in history:
        sender = msg.get("sender", "")
        text = msg.get("text", "")
        is_user = 0 if msg.get("isBot", True) else 1
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO chatlog (interview_id, sender, text, is_user, timestamp) VALUES (%s, %s, %s, %s, %s)",
            (interview_id, sender, text, is_user, now)
        )
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"result": "success"})

@app.route('/api/interview_result', methods=['GET'])
def interview_result():
    interview_id = request.args.get("interview_id")
    if not interview_id:
        return jsonify({"error": "Missing interview_id"}), 400
    conn = mysql.connector.connect(database=DB_NAME, **DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM interviews WHERE id=%s", (interview_id,))
    interview = cursor.fetchone()
    cursor.execute("SELECT * FROM questions WHERE interview_id=%s", (interview_id,))
    questions = cursor.fetchall()
    cursor.execute("SELECT sender, text, is_user FROM chatlog WHERE interview_id=%s ORDER BY id ASC", (interview_id,))
    chatlog_rows = cursor.fetchall()
    chatlog = []
    for row in chatlog_rows:
        chatlog.append({
            "sender": row["sender"],
            "text": row["text"],
            "isBot": not bool(row["is_user"])
        })
    cursor.close()
    conn.close()
    score = round(interview["score"], 2) if interview["score"] is not None else 0

    feedback, resources = generate_gemini_feedback_and_resources(interview, questions)

    return jsonify({
        "score": score,
        "total_questions": interview["total_questions"],
        "questions": questions,
        "history": chatlog,
        "email": interview["email"],
        "feedback": feedback,
        "resources": resources,
        "cheated": False
    })

@app.route('/api/get_interviews_by_email', methods=['GET'])
def get_interviews_by_email():
    email = request.args.get("email")
    if not email:
        return jsonify({"error": "Missing email parameter"}), 400
    conn = mysql.connector.connect(database=DB_NAME, **DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, major, degree_level, difficulty, score, total_questions, datetime FROM interviews WHERE email=%s ORDER BY datetime DESC", (email,))
    interviews = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify({
        "interviews": interviews
    })

def generate_pdf_report(interview, questions, pdf_path):
    # Define custom styles with dark blue theme
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'Title',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#1A365D'),
        alignment=TA_CENTER,
        spaceAfter=20
    )
    header_style = ParagraphStyle(
        'Header',
        parent=styles['Heading2'],
        fontSize=16,
        textColor=colors.HexColor('#1A365D'),
        spaceBefore=12,
        spaceAfter=6
    )
    subheader_style = ParagraphStyle(
        'Subheader',
        parent=styles['Heading3'],
        fontSize=14,
        textColor=colors.HexColor('#2C5282'),
        spaceBefore=10,
        spaceAfter=4
    )
    normal_style = ParagraphStyle(
        'Normal',
        parent=styles['Normal'],
        fontSize=11,
        textColor=colors.black,
        leading=14,
        spaceAfter=6
    )
    answer_style = ParagraphStyle(
        'Answer',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.HexColor('#4A5568'),
        backColor=colors.HexColor('#F7FAFC'),
        borderColor=colors.HexColor('#E2E8F0'),
        borderWidth=1,
        borderPadding=(5, 5, 5),
        leading=13,
        spaceAfter=8
    )
    score_style = ParagraphStyle(
        'Score',
        parent=styles['Heading2'],
        fontSize=18,
        textColor=colors.HexColor('#2B6CB0'),
        alignment=TA_CENTER,
        spaceBefore=10,
        spaceAfter=20
    )

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        rightMargin=inch/2,
        leftMargin=inch/2,
        topMargin=inch/2,
        bottomMargin=inch/2
    )

    elements = []
    elements.append(Paragraph("INTERVIEW PERFORMANCE REPORT", title_style))
    elements.append(Spacer(1, 0.2*inch))
    elements.append(Paragraph("Candidate Information", header_style))
    elements.append(Paragraph(f"<b>Name:</b> {interview['name']}", normal_style))
    elements.append(Paragraph(f"<b>Email:</b> {interview['email']}", normal_style))
    elements.append(Paragraph(f"<b>Major:</b> {interview['major'].title().replace('_', ' ')}", normal_style))
    elements.append(Paragraph(f"<b>Degree Level:</b> {interview['degree_level'].title()}", normal_style))
    elements.append(Paragraph(f"<b>Difficulty:</b> {interview['difficulty'].title()}", normal_style))
    elements.append(Paragraph(f"<b>Date:</b> {interview['datetime']}", normal_style))
    elements.append(Spacer(1, 0.2*inch))

    score = round(interview['score'], 2) if interview['score'] is not None else 0
    total = interview['total_questions']
    percentage = (score / total) * 100 if total > 0 else 0

    elements.append(Paragraph("Performance Summary", header_style))
    elements.append(Paragraph(f"Final Score: {score} / {total} ({percentage:.1f}%)", score_style))
    performance_bar = "▓" * int(percentage/10) + "░" * (10 - int(percentage/10))
    elements.append(Paragraph(f"<font color='#2B6CB0'>{performance_bar}</font>", 
                           ParagraphStyle(name='Bar', alignment=TA_CENTER, fontSize=14)))
    elements.append(Spacer(1, 0.3*inch))
    elements.append(Paragraph("Question Breakdown", header_style))
    for idx, q in enumerate(questions):
        elements.append(Paragraph(f"Question {idx+1}", subheader_style))
        elements.append(Paragraph(q['question_text'], normal_style))
        elements.append(Paragraph("<b>Model Answer:</b>", normal_style))
        elements.append(Paragraph(q['answer'], answer_style))
        user_answer = q['user_answer'] if q['user_answer'] else "[No answer provided]"
        elements.append(Paragraph("<b>Your Answer:</b>", normal_style))
        elements.append(Paragraph(user_answer, answer_style))
        points = q['points'] if q['points'] is not None else 0
        points_color = "#38A169" if points >= 0.7 else "#E53E3E" if points < 0.3 else "#DD6B20"
        elements.append(Paragraph(f"<b>Points Awarded:</b> <font color='{points_color}'>{points:.2f}</font>", normal_style))
        elements.append(Spacer(1, 0.15*inch))
    doc.build(elements)

def send_pdf_email(to_email, pdf_path, interview):
    msg = EmailMessage()
    msg['Subject'] = "Your Interviewy Interview Report"
    msg['From'] = GMAIL_SENDER
    msg['To'] = to_email
    msg.set_content("Your Interviewy interview report is attached.")
    with open(pdf_path, "rb") as f:
        file_data = f.read()
        msg.add_attachment(file_data, maintype='application', subtype='pdf', filename="Interviewy_Report.pdf")
    with smtplib.SMTP(GMAIL_SMTP, GMAIL_PORT) as smtp:
        smtp.starttls()
        smtp.login(GMAIL_SENDER, GMAIL_PASSWORD)
        smtp.send_message(msg)

@app.route('/api/generate_pdf', methods=['POST'])
def generate_pdf():
    data = request.json
    interview_id = data.get("interview_id")
    if not interview_id:
        return jsonify({"error": "Missing interview_id"}), 400
    conn = mysql.connector.connect(database=DB_NAME, **DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM interviews WHERE id=%s", (interview_id,))
    interview = cursor.fetchone()
    cursor.execute("SELECT * FROM questions WHERE interview_id=%s", (interview_id,))
    questions = cursor.fetchall()
    cursor.close()
    conn.close()
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
            pdf_path = temp_pdf.name
        generate_pdf_report(interview, questions, pdf_path)
        send_pdf_email(interview["email"], pdf_path, interview)
        os.remove(pdf_path)
        return jsonify({"success": True, "message": "PDF generated and emailed successfully!"})
    except Exception as e:
        print("PDF/email error:", e)
        return jsonify({"error": "Failed to generate or send PDF."}), 500

if __name__ == '__main__':
    app.run(debug=True)