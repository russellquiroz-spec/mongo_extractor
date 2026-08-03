from mongo_extractor.types import AppConfig, MongoConfig, SSHTunnelParams, SSMTunnelParams


def test_types_construct():
    ssh = SSHTunnelParams(
        host="h", port=22, user="u", pkey_path="/tmp/k.pem",
        local_port=27018, remote_host="r", remote_port=27017,
    )
    ssm = SSMTunnelParams(
        aws_region="us-east-2", target="i-xxx", local_port=27017,
        remote_host="r", remote_port=27017, ssm_command="aws ssm ...",
    )
    cfg = MongoConfig(
        tunnel="ssh", db="transactions", uri_template="mongodb://{user}:{password}@localhost:27018/",
        user="u", password="p", warmup_s=1.0, ssh=ssh, ssm=None,
    )
    cfg2 = MongoConfig(
        tunnel="ssm", db="BNPL", uri_template="mongodb://{user}:{password}@localhost:27017/",
        user="u", password="p", warmup_s=10.0, ssh=None, ssm=ssm,
    )
    app = AppConfig(log_level="INFO", output_dir="./output", server_selection_timeout_ms=20000)

    assert cfg.tunnel == "ssh"
    assert cfg.ssh is ssh
    assert cfg2.tunnel == "ssm"
    assert cfg2.ssm is ssm
    assert app.server_selection_timeout_ms == 20000
