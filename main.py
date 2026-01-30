import os
from fastapi import FastAPI, HTTPException, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime
import random
from fastapi.middleware.cors import CORSMiddleware
import bcrypt
import logging
from typing import Optional
from stock_scraper import get_price_data

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 配置
PORT = os.getenv("PORT", "8001")
DATABASE_URL = f"sqlite:///./data_store_{PORT}.db"

# 数据库模型
Base = declarative_base()

class DataStore(Base):
    __tablename__ = "data_store"
    id = Column(Integer, primary_key=True, index=True)
    clickId = Column(Integer, unique=True, index=True)
    redirectUrl = Column(String, index=True)
    userName = Column(String, index=True)

class ApiCallLog(Base):
    __tablename__ = "api_call_log"
    id = Column(Integer, primary_key=True, index=True)
    endpoint = Column(String, index=True)
    ip_address = Column(String, index=True)
    status_code = Column(Integer, index=True)
    timestamp = Column(String, index=True)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)

# 数据库引擎和会话
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建所有表
Base.metadata.create_all(bind=engine)

# 依赖项
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 初始化管理员账号
def initialize_admin_user():
    db = SessionLocal()
    try:
        existing_user = db.query(User).filter(User.username == "admin").first()
        if not existing_user:
            password = "Mm123567.."
            password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            admin_user = User(username="admin", password_hash=password_hash)
            db.add(admin_user)
            db.commit()
            logger.info("管理员账号初始化成功")
        else:
            logger.info("管理员账号已存在，跳过初始化")
    except Exception as e:
        logger.error(f"初始化管理员账号失败: {str(e)}")
        db.rollback()
    finally:
        db.close()

initialize_admin_user()

# FastAPI应用
app = FastAPI(
    title="管理后台系统",
    description="分流链接管理和API监控系统",
    version="1.0.0",
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制为具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 模板和静态文件
templates = Jinja2Templates(directory="templates")

# 辅助函数
def log_api_call(db: Session, endpoint: str, ip_address: str, status_code: int):
    try:
        log_entry = ApiCallLog(
            endpoint=endpoint,
            ip_address=ip_address,
            status_code=status_code,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        db.add(log_entry)
        db.commit()
    except Exception as e:
        logger.error(f"记录API调用日志失败: {str(e)}")
        db.rollback()

# 路由
@app.get("/api/stock/{code}", response_model=dict, summary="获取指定股票代码的股价数据")
async def stock_api_endpoint(request: Request, code: str, db: Session = Depends(get_db)):
    ip_address = request.client.host if request.client else "unknown"
    try:
        stock_data = await get_price_data(code)
        print(stock_data)  # 调试输出
        
        # 记录API调用日志
        log_api_call(db, f"/api/stock/{code}", ip_address, stock_data.get("code", 200))
        
        if stock_data["code"] != 200:
            raise HTTPException(
                status_code=stock_data["code"] if stock_data["code"] > 0 else 500,
                detail=stock_data["msg"]
            )
            
        return stock_data
        
    except Exception as e:
        logger.error(f"API调用失败: {str(e)}")  # 记录详细异常信息
        log_api_call(db, f"/api/stock/{code}", ip_address, 500)
        raise HTTPException(status_code=500, detail=str(e))
        
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.username == username).first()
    if not user or not bcrypt.checkpw(password.encode('utf-8'), user.password_hash.encode('utf-8')):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "用户名或密码错误"
        })

    response = RedirectResponse(url="/admin", status_code=302)
    response.set_cookie(key="logged_in", value="true", httponly=True)
    return response

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("logged_in")
    return response

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    page: int = 1,
    filter_endpoint: str = "/api/tokenId",
    active_section: str = "link-management",
    db: Session = Depends(get_db)
):
    if request.cookies.get("logged_in") != "true":
        return RedirectResponse(url="/login")
    
    data_store = db.query(DataStore).order_by(DataStore.id.desc()).all()

    logs_query = db.query(ApiCallLog).filter(ApiCallLog.endpoint == filter_endpoint)
    total_logs = logs_query.count()
    logs_per_page = 16
    total_pages = max(1, (total_logs + logs_per_page - 1) // logs_per_page)
    current_page = min(max(1, page), total_pages)
    api_logs = logs_query.order_by(ApiCallLog.timestamp.desc()).offset(
        (current_page - 1) * logs_per_page
    ).limit(logs_per_page).all()

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "data_store": data_store,
        "api_logs": api_logs,
        "current_page": current_page,
        "total_pages": total_pages,
        "filter_endpoint": filter_endpoint,
        "active_section": active_section
    })

@app.post("/admin/create")
async def create_data(
    request: Request,
    redirectUrl: str = Form(...),
    userName: str = Form(...),
    db: Session = Depends(get_db)
):
    try:
        # 验证输入
        if not redirectUrl.startswith(("http://", "https://")):
            raise ValueError("URL必须以http://或https://开头")
        
        if len(userName.strip()) < 2:
            raise ValueError("用户名至少需要2个字符")

        # 生成唯一clickId
        clickId = random.randint(1000000000000, 9999999999999)
        while db.query(DataStore).filter(DataStore.clickId == clickId).first():
            clickId = random.randint(1000000000000, 9999999999999)

        # 创建记录
        new_data = DataStore(
            clickId=clickId,
            redirectUrl=redirectUrl.strip(),
            userName=userName.strip()
        )
        db.add(new_data)
        db.commit()
        logger.info(f"成功添加新链接: {userName} -> {redirectUrl}")
        
        return RedirectResponse(url="/admin", status_code=302)
    
    except ValueError as ve:
        logger.warning(f"无效输入: {str(ve)}")
        return templates.TemplateResponse("admin.html", {
            "request": request,
            "error": str(ve),
            "data_store": db.query(DataStore).all(),
            "api_logs": db.query(ApiCallLog).limit(16).all()
        })
    except Exception as e:
        db.rollback()
        logger.error(f"添加链接失败: {str(e)}")
        return templates.TemplateResponse("admin.html", {
            "request": request,
            "error": "服务器错误，请稍后再试",
            "data_store": db.query(DataStore).all(),
            "api_logs": db.query(ApiCallLog).limit(16).all()
        })

@app.post("/admin/update")
async def update_data(
    request: Request,
    clickId: int = Form(...),
    redirectUrl: str = Form(...),
    userName: str = Form(...),
    db: Session = Depends(get_db)
):
    try:
        data_to_update = db.query(DataStore).filter(DataStore.clickId == clickId).first()
        if not data_to_update:
            raise ValueError("指定的链接不存在")
        
        data_to_update.redirectUrl = redirectUrl.strip()
        data_to_update.userName = userName.strip()
        db.commit()
        logger.info(f"已更新链接: {clickId}")
        return RedirectResponse(url="/admin", status_code=302)
    except Exception as e:
        db.rollback()
        logger.error(f"更新链接失败: {str(e)}")
        return templates.TemplateResponse("admin.html", {
            "request": request,
            "error": str(e),
            "data_store": db.query(DataStore).all(),
            "api_logs": db.query(ApiCallLog).limit(16).all()
        })

@app.post("/admin/delete")
async def delete_data(
    request: Request,
    clickId: int = Form(...),
    db: Session = Depends(get_db)
):
    try:
        data_to_delete = db.query(DataStore).filter(DataStore.clickId == clickId).first()
        if not data_to_delete:
            raise ValueError("指定的链接不存在")
        
        db.delete(data_to_delete)
        db.commit()
        logger.info(f"已删除链接: {clickId}")
        return RedirectResponse(url="/admin", status_code=302)
    except Exception as e:
        db.rollback()
        logger.error(f"删除链接失败: {str(e)}")
        return templates.TemplateResponse("admin.html", {
            "request": request,
            "error": str(e),
            "data_store": db.query(DataStore).all(),
            "api_logs": db.query(ApiCallLog).limit(16).all()
        })

@app.get("/api/tokenId")
async def page_loading(
    request: Request,
    gad_source: Optional[str] = None,
    token: Optional[str] = None,
    db: Session = Depends(get_db)
):
    ip_address = request.client.host if request.client else "unknown"
    try:
        if not gad_source:
            log_api_call(db, "/api/tokenId", ip_address, 400)
            raise HTTPException(status_code=400, detail={"msg": "data error", "code": 400})

        data_store = db.query(DataStore).all()
        if not data_store:
            log_api_call(db, "/api/tokenId", ip_address, 404)
            raise HTTPException(status_code=404, detail={"msg": "data error", "code": 404})

        selected_data = random.choice(data_store)
        log_api_call(db, "/api/tokenId", ip_address, 200)
        
        return {
            "msg": "success",
            "code": 200,
            "clickId": selected_data.clickId
        }
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        log_api_call(db, "/api/tokenId", ip_address, 500)
        raise HTTPException(status_code=500, detail={"msg": "data error", "code": 500})

@app.get("/api/endurl")
async def click_btn(
    request: Request,
    tokenId: int,
    db: Session = Depends(get_db)
):
    ip_address = request.client.host if request.client else "unknown"
    try:
        data = db.query(DataStore).filter(DataStore.clickId == tokenId).first()
        if not data:
            log_api_call(db, "/api/endurl", ip_address, 404)
            return {"msg": "指定的 tokenId 不存在", "code": 404}

        log_api_call(db, "/api/endurl", ip_address, 200)
        return {
            "msg": "success",
            "code": 200,
            "data": {
                "redirectUrl": data.redirectUrl,
                "clickId": data.clickId,
                "userName": data.userName
            }
        }
    except Exception as e:
        log_api_call(db, "/api/endurl", ip_address, 500)
        return {"msg": "服务器错误", "code": 500}

app.mount("/", StaticFiles(directory="index", html=True), name="static")

if __name__ == "__main__":
    if not os.path.exists("index"):
        os.makedirs("index")
        with open("index/index.html", "w") as f:
            f.write("<h1>Welcome</h1>")
    
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(PORT))