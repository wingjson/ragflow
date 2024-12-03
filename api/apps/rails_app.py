
import diskcache as dc
import pymysql
import uuid
import json
import requests
class Cache:
    def __init__(self, cache_dir='/home/railmodel/Rag/ragflow/cache'):
        self.cache = dc.Cache(cache_dir)
    
    def set_value(self, key, value, expire=None):
        self.cache.set(key, value, expire=expire)
    
    def get_value(self, key, default=None):
        return self.cache.get(key, default=default)
    
    def delete_value(self, key):
        del self.cache[key]
    
    def clear_cache(self):
        self.cache.clear()

    def close_cache(self):
        self.cache.close()


class MySQLDatabase:
    def __init__(self, host="192.168.96.183", user="mt", password="rdsp@CARS2022", database="rail_gpt", port=3306):
        self.host = host
        self.user = user
        self.password = password
        self.database = database
        self.port = port
        self.connection = None

    def connect(self):
        """建立数据库连接"""
        try:
            self.connection = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                port=self.port,
                cursorclass=pymysql.cursors.DictCursor
            )
            print("数据库连接成功")
        except pymysql.MySQLError as e:
            print(f"数据库连接失败: {e}")
            raise

    def close(self):
        """关闭数据库连接"""
        if self.connection:
            self.connection.close()
            print("数据库连接已关闭")

    def execute_query(self, query, params=None):
        """执行查询操作"""
        with self.connection.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()

    def execute_update(self, query, params=None):
        """执行更新操作"""
        with self.connection.cursor() as cursor:
            cursor.execute(query, params)
            self.connection.commit()

    def execute_many(self, query, params_list):
        """执行批量更新操作"""
        with self.connection.cursor() as cursor:
            cursor.executemany(query, params_list)
            self.connection.commit()

    def insert(self, query, params):
        """插入记录"""
        self.execute_update(query, params)

    def update(self, query, params):
        """更新记录"""
        self.execute_update(query, params)

    def delete(self, query, params):
        """删除记录"""
        self.execute_update(query, params)


def deal_railinfo(user_info, qa_content, answer):
    db = MySQLDatabase()
    account = user_info['uName']
    sessionId = user_info['sessionId']
    qauuid = str(uuid.uuid4())  # 替换为实际的 UUID 生成逻辑


    db.connect()

    try:
        # 查询操作
        results = db.execute_query("SELECT * FROM userinfo WHERE account = %s", (account,))
        print(results)
        user_id = results[0]['id']

        try:
        # 插入操作1
            insert_query_q = f"""
            INSERT INTO `qa_record`
            (account, qa_content, qa_type, remark, session_id, use_flag, user_id, qauuid) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            db.insert(insert_query_q, (account, qa_content, 0, '', sessionId, 1, user_id, qauuid))
            print("Insert query_q successful.")
        except Exception as e:
            print(f"Insert query_q failed: {e}")

        try:
        # 插入操作2
            insert_query_a = f"""
            INSERT INTO `qa_record`
            (account, qa_content, qa_type, remark, session_id, use_flag, user_id, qauuid) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            db.insert(insert_query_a, (account, answer['answer'], 1, json.dumps(answer['reference']), sessionId, 1, user_id, qauuid))
            print("Insert query_a successful.")
        except Exception as e:
            print(f"Insert query_a failed: {e}")


    finally:
        db.close()

def check_login(token):
    url = "http://192.168.96.182/gpt/v1/trans/get-user-info"  # 替换为实际的URL

    # 设置请求头
    headers = {
        "Authorization": token
    }

    # 发送GET请求
    response = requests.get(url, headers=headers,verify=False)
    print(response)
    # 检查响应状态码
    if response.status_code == 200:
        # 如果请求成功，获取返回的JSON数据
        data = response.json()
        return True,data
    else:
        data = response.json()
        return False,data