from kubernetes import client, config
from kubernetes.client.configuration import Configuration
import urllib3

config.load_kube_config(config_file="/Users/lizhengyang/MyProjects/myscripts/kubeconfig.yaml")

# # # 禁用 SSL 验证
# # # c = Configuration.get_default_copy()
# # # c.verify_ssl = False
# # # client.Configuration.set_default(c)
#
v1 = client.CoreV1Api()
print([ns.metadata.name for ns in v1.list_namespace().items])
# #

# # from kubernetes import client
# # from kubernetes.client import Configuration
# # import urllib3
# #
# # urllib3.disable_warnings()
# #
# # # 👇 你的 token 原样粘贴（不要换行，不要加 # 或其他标点）
# # token = "eyJhbGciOiJSUzI1NiIsImtpZCI6Im04SEFZZHAtQ29Ec2RqN1RxSGtkaEVfQWtzazBuVDNrMlF5MlB4YW9qYXMifQ.eyJpc3MiOiJrdWJlcm5ldGVzL3NlcnZpY2VhY2NvdW50Iiwia3ViZXJuZXRlcy5pby9zZXJ2aWNlYWNjb3VudC9uYW1lc3BhY2UiOiJrdWJlLXN5c3RlbSIsImt1YmVybmV0ZXMuaW8vc2VydmljZWFjY291bnQvc2VjcmV0Lm5hbWUiOiJkZWZhdWx0LXRva2VuLTV2dGtmIiwia3ViZXJuZXRlcy5pby9zZXJ2aWNlYWNjb3VudC9zZXJ2aWNlLWFjY291bnQubmFtZSI6ImRlZmF1bHQiLCJrdWJlcm5ldGVzLmlvL3NlcnZpY2VhY2NvdW50L3NlcnZpY2UtYWNjb3VudC51aWQiOiI4ZDY2ODA2NC1mMjBmLTQ2MGUtYjY2Zi01NTM1M2QwNzg1YTEiLCJzdWIiOiJzeXN0ZW06c2VydmljZWFjY291bnQ6a3ViZS1zeXN0ZW06ZGVmYXVsdCJ9.wSlaed8sVu2KsieUntUOSKHdgSvBsEo7orgLUbxTqQUSNrmN63VnCwF3aAOgtxHUhgZGjVKMoaXJzHpvClCmOngPvDI8OO0rv4glMErEb3hLP0CxBIvNWeSNKnLdMrNMOG2BsOD7G1EiOnrxtq7-zC2OmyGrC3j8aRtNidGKrIbp4BOIzTRiEZkxEOEOHV9nteFi3lRJxwqXYvTmWsnk_yBIkYPvBBJ5jciux-8YJJMxIQiJSJfYJkT24enQ9j-EjOXlBZX8KOlmZ1IdWWrzeZsTnxeuKHb-dJUGufkHg0BvPTKRpEhTnlHp8i3eYuhhD0Q5e_uwLdJZryEqUCqVVA/"
# #
# # # 👇 你的 K8s API 地址（注意要是 https://，且能访问）
# # api_server = "http://10.43.86.42:30880/"  # ← 请替换成你真实的地址！
# #
# # config = Configuration()
# # config.host = api_server
# # config.verify_ssl = False  # 若是自签证书需设置为 False
# # config.api_key = {"authorization": f"Bearer {token}"}
# # client.Configuration.set_default(config)
# #
# # v1 = client.CoreV1Api()
# # print(v1.list_pod_for_all_namespaces(watch=False))
# # # response = v1.list_namespace()
# # # print(response)  # 打印完整响应结构
# # # print("正在获取命名空间列表...")
# # # for ns in v1.list_namespace().items:
# # #     print(ns.metadata.name)
# import requests
#
#
# # import requests
# #
# # NODE_IP = "10.43.86.42"  # 替换为你的 node IP
# # PORT = 30880
# # USERNAME = "user"
# # PASSWORD = "1qaz!Qaz"
# #
# # login_url = f"http://{NODE_IP}:{PORT}/kapis/iam.kubesphere.io/v1alpha2/login"
# # resp = requests.post(login_url, json={"username": USERNAME, "password": PASSWORD})
# #
# # print("状态码:", resp.status_code)
# # print("响应内容:", resp.text)
# #
# # if resp.status_code == 200 and "token" in resp.json():
# #     token = resp.json()["token"]
# #     print("✅ 登录成功，token =", token)
# # else:
# #     print("❌ 登录失败")
import base64

print(base64.b64decode("ZXlKaGJHY2lPaUpTVXpJMU5pSXNJbXRwWkNJNkltMDRTRUZaWkhBdFEyOUVjMlJxTjFSeFNHdGthRVZmUVd0emF6QnVWRE5yTWxGNU1sQjRZVzlxWVhNaWZRLmV5SnBjM01pT2lKcmRXSmxjbTVsZEdWekwzTmxjblpwWTJWaFkyTnZkVzUwSWl3aWEzVmlaWEp1WlhSbGN5NXBieTl6WlhKMmFXTmxZV05qYjNWdWRDOXVZVzFsYzNCaFkyVWlPaUpyZFdKbExYTjVjM1JsYlNJc0ltdDFZbVZ5Ym1WMFpYTXVhVzh2YzJWeWRtbGpaV0ZqWTI5MWJuUXZjMlZqY21WMExtNWhiV1VpT2lKdGVTMXpZUzEwYjJ0bGJpSXNJbXQxWW1WeWJtVjBaWE11YVc4dmMyVnlkbWxqWldGalkyOTFiblF2YzJWeWRtbGpaUzFoWTJOdmRXNTBMbTVoYldVaU9pSnRlUzF6WVNJc0ltdDFZbVZ5Ym1WMFpYTXVhVzh2YzJWeWRtbGpaV0ZqWTI5MWJuUXZjMlZ5ZG1salpTMWhZMk52ZFc1MExuVnBaQ0k2SW1ReE1tVmtPV1ZqTFRVMVpHUXROR0V6WkMwNE9HRm1MV0ZoT1Rnd1ltWTVaREF4TkNJc0luTjFZaUk2SW5ONWMzUmxiVHB6WlhKMmFXTmxZV05qYjNWdWREcHJkV0psTFhONWMzUmxiVHB0ZVMxellTSjkudkRyeVN3SHU2WWRKQWxaNHBZWldLZ1pPODBNTmJ5ekI0OWVUTlBpSjlCY183bXhNR25zZVJoOFlzbEsxZkRqSTBGLTg2OG5MRG1KWWNacUxlVWxubGVKLWdERmRYTFVuc2dQME5RcU9BZjdCa3pQS2cxRENYc3R2TWhHWU02ZTVpNlhqd2VCaW1vczdORzYxdktrSk9TWUwwelktWjB4eDFzRGU1RGVrczY1eGVDOUg5ZDNXcnZQbU5DamJENFB1aUEzZ1NZMTRrUVludUdMUWRiaHZsNEstejdvTUIzQ3RwUExtdnlYSk01MDEyUVZRUlp0ekJFSFRvTGdJa2hlWERiR19qOU1KN3ZGYXMxSGtnU1NMWFJoZV9nSDBmaUNvQVNpcDk2OC16S0tUeE96dTh6dmF5Y1FDNUJvQ1FDdjNtQmU0b3VKWlhnN0xuRWE2QlhiZWh3"))