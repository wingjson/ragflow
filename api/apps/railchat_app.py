
#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import base64
import json
import os
import re
from datetime import datetime, timedelta
from flask import jsonify, request, Response
import flask

from elasticsearch_dsl import Q
from api.apps.rails_app import Cache as rCache, deal_railinfo, check_login
from rag.nlp import search

from api.db import FileType, LLMType, ParserType, FileSource, TaskStatus

from api.db.services.llm_service import LLMBundle, TenantLLMService
from flask_login import login_required, current_user

from api.db.db_models import APIToken, Task, File
from api.db.services import duplicate_name
from api.db.services.api_service import APITokenService, API4ConversationService
from api.db.services.dialog_service import DialogService, chat
from api.db.services.document_service import DocumentService, doc_upload_and_parse
from api.db.services.file2document_service import File2DocumentService
from api.db.services.file_service import FileService
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.task_service import queue_tasks, TaskService
from api.db.services.user_service import UserTenantService
from api.settings import RetCode, retrievaler
from api.utils import get_uuid, current_timestamp, datetime_format
from api.utils.api_utils import server_error_response, get_data_error_result, get_json_result, validate_request, \
    generate_confirmation_token

from api.utils.file_utils import filename_type, thumbnail
from api.db.services.dialog_service import DialogService, chat, keyword_extraction
from rag.utils.storage_factory import STORAGE_IMPL
from rag.utils.minio_conn import MINIO

from api.db.services.canvas_service import UserCanvasService
from agent.canvas import Canvas
from functools import partial


@manager.route('/image/<image_id>', methods=['GET'])
# @login_required
def get_image(image_id):
    try:
        bkt, nm = image_id.split("-")
        response = flask.make_response(MINIO.get(bkt, nm))
        response.headers.set('Content-Type', 'image/JPEG')
        return response
    except Exception as e:
        return server_error_response(e)


@manager.route('/document/change_parser', methods=['POST'])
# @login_required
@validate_request("doc_id", "parser_id")
def change_parser():
    req = request.json

    if not DocumentService.accessible(req["doc_id"], current_user.id):
        return get_json_result(
            data=False,
            message='No authorization.',
            code=settings.RetCode.AUTHENTICATION_ERROR
        )
    try:
        e, doc = DocumentService.get_by_id(req["doc_id"])
        if not e:
            return get_data_error_result(message="Document not found!")
        if doc.parser_id.lower() == req["parser_id"].lower():
            if "parser_config" in req:
                if req["parser_config"] == doc.parser_config:
                    return get_json_result(data=True)
            else:
                return get_json_result(data=True)

        if ((doc.type == FileType.VISUAL and req["parser_id"] != "picture")
                or (re.search(
                    r"\.(ppt|pptx|pages)$", doc.name) and req["parser_id"] != "presentation")):
            return get_data_error_result(message="Not supported yet!")

        e = DocumentService.update_by_id(doc.id,
                                         {"parser_id": req["parser_id"], "progress": 0, "progress_msg": "",
                                          "run": TaskStatus.UNSTART.value})
        if not e:
            return get_data_error_result(message="Document not found!")
        if "parser_config" in req:
            DocumentService.update_parser_config(doc.id, req["parser_config"])
        if doc.token_num > 0:
            e = DocumentService.increment_chunk_num(doc.id, doc.kb_id, doc.token_num * -1, doc.chunk_num * -1,
                                                    doc.process_duation * -1)
            if not e:
                return get_data_error_result(message="Document not found!")
            tenant_id = DocumentService.get_tenant_id(req["doc_id"])
            if not tenant_id:
                return get_data_error_result(message="Tenant not found!")
            if settings.docStoreConn.indexExist(search.index_name(tenant_id), doc.kb_id):
                settings.docStoreConn.delete({"doc_id": doc.id}, search.index_name(tenant_id), doc.kb_id)

        return get_json_result(data=True)
    except Exception as e:
        return server_error_response(e)

@manager.route('/document/run', methods=['POST'])
# @login_required
@validate_request("doc_ids", "run")
def run():
     
    token = request.headers.get('Authorization').split()[1]
    objs = APIToken.query(token=token)
    if not objs:
        return get_json_result(
            data=False, retmsg='Token is not valid!"', retcode=RetCode.AUTHENTICATION_ERROR)

    req = request.json
    try:
        for id in req["doc_ids"]:
            info = {"run": str(req["run"]), "progress": 0}
            if str(req["run"]) == TaskStatus.RUNNING.value:
                info["progress_msg"] = ""
                info["chunk_num"] = 0
                info["token_num"] = 0
            DocumentService.update_by_id(id, info)
            # if str(req["run"]) == TaskStatus.CANCEL.value:
            tenant_id = DocumentService.get_tenant_id(id)
            if not tenant_id:
                return get_data_error_result(retmsg="Tenant not found!")
            ELASTICSEARCH.deleteByQuery(
                Q("match", doc_id=id), idxnm=search.index_name(tenant_id))

            if str(req["run"]) == TaskStatus.RUNNING.value:
                TaskService.filter_delete([Task.doc_id == id])
                e, doc = DocumentService.get_by_id(id)
                doc = doc.to_dict()
                doc["tenant_id"] = tenant_id
                bucket, name = File2DocumentService.get_storage_address(doc_id=doc["id"])
                queue_tasks(doc, bucket, name)

        return get_json_result(data=True)
    except Exception as e:
        return server_error_response(e)

@manager.route('/related_questions', methods=['POST'])
# @login_required
@validate_request("question")
def related_questions():
    token = request.headers.get('Authorization')
    login_status,msg= check_login(token)
    if not login_status:
        return jsonify({'message': msg.get('message')}), msg.get('statusCode')

    rag_token = "tkyrag-M2MTkxNjYyOTdlYjExZWZiNTkwZjhmMj"
    objs = APIToken.query(token=rag_token)
    if not objs:
        return get_json_result(
            data=False, retmsg='Token is not valid!"', retcode=RetCode.AUTHENTICATION_ERROR)
    req = request.json
    question = req["question"]
    chat_mdl = TenantLLMService.model_instance(objs[0].tenant_id, LLMType.CHAT)
    prompt = """
Objective: To generate search terms related to the user's search keywords, helping users find more valuable information.
Instructions:
 - Based on the keywords provided by the user, generate 5-10 related search terms.
 - Each search term should be directly or indirectly related to the keyword, guiding the user to find more valuable information.
 - Use common, general terms as much as possible, avoiding obscure words or technical jargon.
 - Keep the term length between 2-4 words, concise and clear.
 - DO NOT translate, use the language of the original keywords.

### Example:
Keywords: Chinese football
Related search terms:
1. Current status of Chinese football
2. Reform of Chinese football
3. Youth training of Chinese football
4. Chinese football in the Asian Cup
5. Chinese football in the World Cup

Reason:
 - When searching, users often only use one or two keywords, making it difficult to fully express their information needs.
 - Generating related search terms can help users dig deeper into relevant information and improve search efficiency. 
 - At the same time, related terms can also help search engines better understand user needs and return more accurate search results.
 
"""
    ans = chat_mdl.chat(prompt, [{"role": "user", "content": f"""
Keywords: {question}
Related search terms:
    """}], {"temperature": 0.9})
    print(ans)
    return get_json_result(data=[re.sub(r"^[0-9]\. ", "", a) for a in ans.split("\n") if re.match(r"^[0-9]\. ", a)])

@manager.route('/rails_completions', methods=['POST'])
@validate_request("rail_conversation_id", "messages")
def rails_completions():
    token = request.headers.get('Authorization')
    login_status,msg= check_login(token)
    if not login_status:
        return jsonify({'message': msg.get('message')}), msg.get('statusCode')
        
    reqs = request.json
    rag_token = "tkyrag-M2MTkxNjYyOTdlYjExZWZiNTkwZjhmMj"
    objs = APIToken.query(token=rag_token)
    if not objs:
        return get_json_result(
            data=False, retmsg='Token is not valid!"', retcode=RetCode.AUTHENTICATION_ERROR)
    
   
    
    ###############铁科###############################
    # question_list = base64.b64decode(reqs["messages"]).decode('utf-8')[-1]['content']
    rail_user_info_str = base64.b64decode(reqs["u"]).decode('utf-8')
    messages_str = base64.b64decode(reqs["messages"]).decode('utf-8')
    
    # 将字符串转换为字典
    rail_user_info = json.loads(rail_user_info_str)
    messages = json.loads(messages_str)
    
    reqs['messages'] = messages
    question_list = reqs["messages"][-1]['content']
    print(rail_user_info)
    print(messages)
    del reqs["u"]
    rail_cache = rCache()
    temp_conversation_id = rail_cache.get_value(reqs["rail_conversation_id"])
    print(temp_conversation_id)
    if temp_conversation_id is None:
        if objs[0].source == "agent":
            e, cvs = UserCanvasService.get_by_id(objs[0].dialog_id)
            if not e:
                return server_error_response("canvas not found.")
            if not isinstance(cvs.dsl, str):
                cvs.dsl = json.dumps(cvs.dsl, ensure_ascii=False)
            canvas = Canvas(cvs.dsl, objs[0].tenant_id)
            conv = {
                "id": get_uuid(),
                "dialog_id": cvs.id,
                "user_id": request.args.get("user_id", ""),
                "message": [{"role": "assistant", "content": canvas.get_prologue()}],
                "source": "agent"
            }
            API4ConversationService.save(**conv)
        else:
            e, dia = DialogService.get_by_id(objs[0].dialog_id)
            if not e:
                return get_data_error_result(retmsg="Dialog not found")
            conv = {
                        "id": get_uuid(),
                        "dialog_id": dia.id,
                        "user_id": request.args.get("user_id", ""),
                        "message": [{"role": "assistant", "content": dia.prompt_config["prologue"]}],
                    }
            API4ConversationService.save(**conv)
        temp_conversation_id = conv["id"]
        rail_cache.set_value(reqs["rail_conversation_id"], temp_conversation_id)
    req = reqs
    req['conversation_id'] = temp_conversation_id
    del req["rail_conversation_id"]
    #####################################################
    e, conv = API4ConversationService.get_by_id(temp_conversation_id)
    if not e:
        return get_data_error_result(retmsg="Conversation not found!")
    if "quote" not in req: req["quote"] = False

    msg = []
    for m in req["messages"]:
        if m["role"] == "system":
            continue
        if m["role"] == "assistant" and not msg:
            continue
        msg.append({"role": m["role"], "content": m["content"]})

    def fillin_conv(ans):
        nonlocal conv
        if not conv.reference:
            conv.reference.append(ans["reference"])
        else:
            conv.reference[-1] = ans["reference"]
        conv.message[-1] = {"role": "assistant", "content": ans["answer"]}

    def rename_field(ans):
        reference = ans['reference']
        if not isinstance(reference, dict):
            return
        for chunk_i in reference.get('chunks', []):
            if 'docnm_kwd' in chunk_i:
                chunk_i['doc_name'] = chunk_i['docnm_kwd']
                chunk_i.pop('docnm_kwd')
  
    try:
        if conv.source == "agent":
            print('--------------------agent----------------------------------------')
            stream = req.get("stream", True)
            conv.message.append(msg[-1])
            e, cvs = UserCanvasService.get_by_id(conv.dialog_id)
            if not e:
                return server_error_response("canvas not found.")
            del req["conversation_id"]
            del req["messages"]

            if not isinstance(cvs.dsl, str):
                cvs.dsl = json.dumps(cvs.dsl, ensure_ascii=False)

            if not conv.reference:
                conv.reference = []
            conv.message.append({"role": "assistant", "content": ""})
            conv.reference.append({"chunks": [], "doc_aggs": []})

            final_ans = {"reference": [], "content": ""}
            canvas = Canvas(cvs.dsl, objs[0].tenant_id)

            canvas.messages.append(msg[-1])
            canvas.add_user_input(msg[-1]["content"])
            if reqs['kbs_ids'] is not None:
                answer = canvas.run(stream=stream,kbs_ids=reqs['kbs_ids'])
            else:
                answer = canvas.run(stream=stream)
            assert answer is not None, "Nothing. Is it over?"

            if stream:
                assert isinstance(answer, partial), "Nothing. Is it over?"

                def sse():
                    nonlocal answer, cvs, conv
                    ans_list = []
                    try:
                        for ans in answer():
                            for k in ans.keys():
                                final_ans[k] = ans[k]
                            ans = {"answer": ans["content"], "reference": ans.get("reference", [])}
                            fillin_conv(ans)
                            rename_field(ans)
                            ans_list.append(ans)
                            yield "data:" + json.dumps({"retcode": 0, "retmsg": "", "data": ans},
                                                       ensure_ascii=False) + "\n\n"

                        canvas.messages.append({"role": "assistant", "content": final_ans["content"]})
                        if final_ans.get("reference"):
                            canvas.reference.append(final_ans["reference"])
                        cvs.dsl = json.loads(str(canvas))
                        API4ConversationService.append_message(conv.id, conv.to_dict())
                    except Exception as e:
                        yield "data:" + json.dumps({"retcode": 500, "retmsg": str(e),
                                                    "data": {"answer": "**ERROR**: " + str(e), "reference": []}},
                                                   ensure_ascii=False) + "\n\n"
                    if ans_list:
                        last_ans = ans_list[-1]  # 获取列表中的最后一条记录
                        # print("最后一条记录:", last_ans)   
                        # print('============================',question_list)
                        deal_railinfo(rail_user_info,question_list,last_ans)
                    yield "data:" + json.dumps({"retcode": 0, "retmsg": "", "data": True}, ensure_ascii=False) + "\n\n"

                resp = Response(sse(), mimetype="text/event-stream")
                resp.headers.add_header("Cache-control", "no-cache")
                resp.headers.add_header("Connection", "keep-alive")
                resp.headers.add_header("X-Accel-Buffering", "no")
                resp.headers.add_header("Content-Type", "text/event-stream; charset=utf-8")
                return resp

            final_ans["content"] = "\n".join(answer["content"]) if "content" in answer else ""
            canvas.messages.append({"role": "assistant", "content": final_ans["content"]})
            if final_ans.get("reference"):
                canvas.reference.append(final_ans["reference"])
            cvs.dsl = json.loads(str(canvas))

            result = {"answer": final_ans["content"], "reference": final_ans.get("reference", [])}
            fillin_conv(result)
            API4ConversationService.append_message(conv.id, conv.to_dict())
            rename_field(result)
            return get_json_result(data=result)
        
        #******************For dialog******************
        conv.message.append(msg[-1])
        e, dia = DialogService.get_by_id(conv.dialog_id)
        if not e:
            return get_data_error_result(retmsg="Dialog not found!")
        del req["conversation_id"]
        del req["messages"]

        if not conv.reference:
            conv.reference = []
        conv.message.append({"role": "assistant", "content": ""})
        conv.reference.append({"chunks": [], "doc_aggs": []})

        def stream():
            nonlocal dia, msg, req, conv
            ans_list = []
            try:
                for ans in chat(dia, msg, True, **req):
                    fillin_conv(ans)
                    rename_field(ans)
                    ans_list.append(ans)
                    # print(ans)
                    # print(type(ans))
                    # print('============================')
                    yield "data:" + json.dumps({"retcode": 0, "retmsg": "", "data": ans},
                                               ensure_ascii=False) + "\n\n"
                API4ConversationService.append_message(conv.id, conv.to_dict())
            except Exception as e:
                yield "data:" + json.dumps({"retcode": 500, "retmsg": str(e),
                                            "data": {"answer": "**ERROR**: " + str(e), "reference": []}},
                                           ensure_ascii=False) + "\n\n"
            if ans_list:
                last_ans = ans_list[-1]  # 获取列表中的最后一条记录
                # print("最后一条记录:", last_ans)   
                # print('============================',question_list)
                deal_railinfo(rail_user_info,question_list,last_ans)

            yield "data:" + json.dumps({"retcode": 0, "retmsg": "", "data": True}, ensure_ascii=False) + "\n\n"

        if req.get("stream", True):
            resp = Response(stream(), mimetype="text/event-stream")
            resp.headers.add_header("Cache-control", "no-cache")
            resp.headers.add_header("Connection", "keep-alive")
            resp.headers.add_header("X-Accel-Buffering", "no")
            resp.headers.add_header("Content-Type", "text/event-stream; charset=utf-8")
            return resp
            
        answer = None
        for ans in chat(dia, msg, **req):
            # print(ans)
            # print("wokankannnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn")
            answer = ans
            fillin_conv(ans)
            API4ConversationService.append_message(conv.id, conv.to_dict())
            break
        rename_field(answer)
        return get_json_result(data=answer)

    except Exception as e:
        return server_error_response(e)
    

@manager.route('/rails_open', methods=['POST'])
@validate_request("messages","type")
def rails_completions_open():
    # token = request.headers.get('Authorization')
    # login_status,msg= check_login(token)
    # if not login_status:
    #     return jsonify({'message': msg.get('message')}), msg.get('statusCode')
        
    reqs = request.json
    if reqs["type"] == 1:
        rag_token = "tkyrag-IzNjkzZmJjOWIzNzExZWY5YjU1ZjhmMj"
    else:
        return get_json_result(
            data=False, retmsg='Token is not valid!"', retcode=RetCode.AUTHENTICATION_ERROR)
    objs = APIToken.query(token=rag_token)
    if not objs:
        return get_json_result(
            data=False, retmsg='Token is not valid!"', retcode=RetCode.AUTHENTICATION_ERROR)
    
    ###############铁科###############################
    messages_str = base64.b64decode(reqs["messages"]).decode('utf-8')
    
    # 将字符串转换为字典
    # rail_user_info = json.loads(rail_user_info_str)
    messages = json.loads(messages_str)
    
    reqs['messages'] = messages
    question_list = reqs["messages"][-1]['content']
    # print(rail_user_info)
    print(messages)
    del reqs["u"]
    rail_cache = rCache()
    temp_conversation_id = None
    if temp_conversation_id is None:
        if objs[0].source == "agent":
            e, cvs = UserCanvasService.get_by_id(objs[0].dialog_id)
            if not e:
                return server_error_response("canvas not found.")
            if not isinstance(cvs.dsl, str):
                cvs.dsl = json.dumps(cvs.dsl, ensure_ascii=False)
            canvas = Canvas(cvs.dsl, objs[0].tenant_id)
            conv = {
                "id": get_uuid(),
                "dialog_id": cvs.id,
                "user_id": request.args.get("user_id", ""),
                "message": [{"role": "assistant", "content": canvas.get_prologue()}],
                "source": "agent"
            }
            API4ConversationService.save(**conv)
        else:
            e, dia = DialogService.get_by_id(objs[0].dialog_id)
            if not e:
                return get_data_error_result(retmsg="Dialog not found")
            conv = {
                        "id": get_uuid(),
                        "dialog_id": dia.id,
                        "user_id": request.args.get("user_id", ""),
                        "message": [{"role": "assistant", "content": dia.prompt_config["prologue"]}],
                    }
            API4ConversationService.save(**conv)
        temp_conversation_id = conv["id"]
        rail_cache.set_value(reqs["rail_conversation_id"], temp_conversation_id)
    req = reqs
    req['conversation_id'] = temp_conversation_id
    del req["rail_conversation_id"]
    #####################################################
    e, conv = API4ConversationService.get_by_id(temp_conversation_id)
    if not e:
        return get_data_error_result(retmsg="Conversation not found!")
    if "quote" not in req: req["quote"] = False

    msg = []
    for m in req["messages"]:
        if m["role"] == "system":
            continue
        if m["role"] == "assistant" and not msg:
            continue
        msg.append({"role": m["role"], "content": m["content"]})

    def fillin_conv(ans):
        nonlocal conv
        if not conv.reference:
            conv.reference.append(ans["reference"])
        else:
            conv.reference[-1] = ans["reference"]
        conv.message[-1] = {"role": "assistant", "content": ans["answer"]}

    def rename_field(ans):
        reference = ans['reference']
        if not isinstance(reference, dict):
            return
        for chunk_i in reference.get('chunks', []):
            if 'docnm_kwd' in chunk_i:
                chunk_i['doc_name'] = chunk_i['docnm_kwd']
                chunk_i.pop('docnm_kwd')
  
    try:
        if conv.source == "agent":
            print('--------------------agent----------------------------------------')
            stream = req.get("stream", True)
            conv.message.append(msg[-1])
            e, cvs = UserCanvasService.get_by_id(conv.dialog_id)
            if not e:
                return server_error_response("canvas not found.")
            del req["conversation_id"]
            del req["messages"]

            if not isinstance(cvs.dsl, str):
                cvs.dsl = json.dumps(cvs.dsl, ensure_ascii=False)

            if not conv.reference:
                conv.reference = []
            conv.message.append({"role": "assistant", "content": ""})
            conv.reference.append({"chunks": [], "doc_aggs": []})

            final_ans = {"reference": [], "content": ""}
            canvas = Canvas(cvs.dsl, objs[0].tenant_id)

            canvas.messages.append(msg[-1])
            canvas.add_user_input(msg[-1]["content"])
            answer = canvas.run(stream=stream)

            assert answer is not None, "Nothing. Is it over?"

            if stream:
                assert isinstance(answer, partial), "Nothing. Is it over?"

                def sse():
                    nonlocal answer, cvs, conv
                    ans_list = []
                    try:
                        for ans in answer():
                            for k in ans.keys():
                                final_ans[k] = ans[k]
                            ans = {"answer": ans["content"], "reference": ans.get("reference", [])}
                            fillin_conv(ans)
                            rename_field(ans)
                            ans_list.append(ans)
                            yield "data:" + json.dumps({"retcode": 0, "retmsg": "", "data": ans},
                                                       ensure_ascii=False) + "\n\n"

                        canvas.messages.append({"role": "assistant", "content": final_ans["content"]})
                        if final_ans.get("reference"):
                            canvas.reference.append(final_ans["reference"])
                        cvs.dsl = json.loads(str(canvas))
                        API4ConversationService.append_message(conv.id, conv.to_dict())
                    except Exception as e:
                        yield "data:" + json.dumps({"retcode": 500, "retmsg": str(e),
                                                    "data": {"answer": "**ERROR**: " + str(e), "reference": []}},
                                                   ensure_ascii=False) + "\n\n"
                    if ans_list:
                        last_ans = ans_list[-1]  # 获取列表中的最后一条记录
                        # print("最后一条记录:", last_ans)   
                        # print('============================',question_list)
                        # deal_railinfo(rail_user_info,question_list,last_ans)
                    yield "data:" + json.dumps({"retcode": 0, "retmsg": "", "data": True}, ensure_ascii=False) + "\n\n"

                resp = Response(sse(), mimetype="text/event-stream")
                resp.headers.add_header("Cache-control", "no-cache")
                resp.headers.add_header("Connection", "keep-alive")
                resp.headers.add_header("X-Accel-Buffering", "no")
                resp.headers.add_header("Content-Type", "text/event-stream; charset=utf-8")
                return resp

            final_ans["content"] = "\n".join(answer["content"]) if "content" in answer else ""
            canvas.messages.append({"role": "assistant", "content": final_ans["content"]})
            if final_ans.get("reference"):
                canvas.reference.append(final_ans["reference"])
            cvs.dsl = json.loads(str(canvas))

            result = {"answer": final_ans["content"], "reference": final_ans.get("reference", [])}
            fillin_conv(result)
            API4ConversationService.append_message(conv.id, conv.to_dict())
            rename_field(result)
            return get_json_result(data=result)
        
        #******************For dialog******************
        conv.message.append(msg[-1])
        e, dia = DialogService.get_by_id(conv.dialog_id)
        if not e:
            return get_data_error_result(retmsg="Dialog not found!")
        del req["conversation_id"]
        del req["messages"]

        if not conv.reference:
            conv.reference = []
        conv.message.append({"role": "assistant", "content": ""})
        conv.reference.append({"chunks": [], "doc_aggs": []})

        def stream():
            nonlocal dia, msg, req, conv
            ans_list = []
            try:
                for ans in chat(dia, msg, True, **req):
                    fillin_conv(ans)
                    rename_field(ans)
                    ans_list.append(ans)
                    # print(ans)
                    # print(type(ans))
                    # print('============================')
                    yield "data:" + json.dumps({"retcode": 0, "retmsg": "", "data": ans},
                                               ensure_ascii=False) + "\n\n"
                API4ConversationService.append_message(conv.id, conv.to_dict())
            except Exception as e:
                yield "data:" + json.dumps({"retcode": 500, "retmsg": str(e),
                                            "data": {"answer": "**ERROR**: " + str(e), "reference": []}},
                                           ensure_ascii=False) + "\n\n"
            if ans_list:
                last_ans = ans_list[-1]  # 获取列表中的最后一条记录
                # print("最后一条记录:", last_ans)   
                # print('============================',question_list)
                deal_railinfo(rail_user_info,question_list,last_ans)

            yield "data:" + json.dumps({"retcode": 0, "retmsg": "", "data": True}, ensure_ascii=False) + "\n\n"

        if req.get("stream", True):
            resp = Response(stream(), mimetype="text/event-stream")
            resp.headers.add_header("Cache-control", "no-cache")
            resp.headers.add_header("Connection", "keep-alive")
            resp.headers.add_header("X-Accel-Buffering", "no")
            resp.headers.add_header("Content-Type", "text/event-stream; charset=utf-8")
            return resp
            
        answer = None
        for ans in chat(dia, msg, **req):
            # print(ans)
            # print("wokankannnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn")
            answer = ans
            fillin_conv(ans)
            API4ConversationService.append_message(conv.id, conv.to_dict())
            break
        rename_field(answer)
        return get_json_result(data=answer)

    except Exception as e:
        return server_error_response(e)
    
@manager.route('/retrieval_rails', methods=['POST'])
@validate_request("question")
def retrieval_rails():
    req = request.json
    kb_ids = req.get("kb_id",[])
    doc_ids = req.get("doc_ids", [])
    question = req.get("question")
    page = int(req.get("page", 1))
    size = int(req.get("size", 30))
    similarity_threshold = float(req.get("similarity_threshold", 0.2))
    vector_similarity_weight = float(req.get("vector_similarity_weight", 0.3))
    top = int(req.get("top_k", 1024))

    try:
        kbs = KnowledgebaseService.get_by_ids(kb_ids)
        embd_nms = list(set([kb.embd_id for kb in kbs]))
        if len(embd_nms) != 1:
            return get_json_result(
                data=False, retmsg='Knowledge bases use different embedding models or does not exist."', retcode=RetCode.AUTHENTICATION_ERROR)

        embd_mdl = TenantLLMService.model_instance(
            kbs[0].tenant_id, LLMType.EMBEDDING.value, llm_name=kbs[0].embd_id)
        rerank_mdl = None
        if req.get("rerank_id"):
            rerank_mdl = TenantLLMService.model_instance(
            kbs[0].tenant_id, LLMType.RERANK.value, llm_name=req["rerank_id"])
        if req.get("keyword", False):
            chat_mdl = TenantLLMService.model_instance(kbs[0].tenant_id, LLMType.CHAT)
            question += keyword_extraction(chat_mdl, question)
        ranks = retrievaler.retrieval(question, embd_mdl, kbs[0].tenant_id, kb_ids, page, size,
            similarity_threshold, vector_similarity_weight, top,
            doc_ids, rerank_mdl=rerank_mdl)
        for c in ranks["chunks"]:
            if "vector" in c:
                del c["vector"]
        return get_json_result(data=ranks)
    except Exception as e:
        if str(e).find("not_found") > 0:
            return get_json_result(data=False, retmsg=f'No chunk found! Check the chunk status please!',
                                   retcode=RetCode.DATA_ERROR)
        return server_error_response(e)