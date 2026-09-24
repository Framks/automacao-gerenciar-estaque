class ServicoIndisponivel(Exception):
    """Falha temporária (limite de uso, rede). O job deve voltar para a fila e ser tentado de novo."""
