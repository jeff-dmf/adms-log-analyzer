from dmslog.cli import main


def test_main_sem_argumentos():
    assert main([]) == 0
