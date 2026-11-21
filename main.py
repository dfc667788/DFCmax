import logging
import os
from decimal import Decimal, ROUND_HALF_UP, localcontext
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4
from typing import Annotated, Literal

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from supabase import Client, create_client

load_dotenv(Path(__file__).with_name(".env"))
app = FastAPI(title="Finia API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv(
        "FINIA_FRONTEND_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173",
    ).split(",") if origin.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
logger = logging.getLogger(__name__)
supabase: Client | None = None
CENTAVO = Decimal("0.01")
PRECISION_TASA = Decimal("0.00000001")
VERSION_CALCULO = "frances-ea-v2"
PLAZO_MAXIMO_MESES = 1200
DIRECTORIO_PROYECTO = Path(__file__).resolve().parent
DIRECTORIO_WEB = DIRECTORIO_PROYECTO / "frontend"
if DIRECTORIO_WEB.is_dir():
    app.mount("/assets", StaticFiles(directory=DIRECTORIO_WEB), name="assets")


@app.get("/simulador", include_in_schema=False)
def interfaz_simulador():
    pagina_actual = DIRECTORIO_PROYECTO / "index.html"
    return FileResponse(pagina_actual if pagina_actual.is_file() else DIRECTORIO_WEB / "index.html")


@app.get("/estilos.css", include_in_schema=False)
def estilos_pagina_actual():
    return FileResponse(DIRECTORIO_PROYECTO / "estilos.css", media_type="text/css")


@app.get("/script.js", include_in_schema=False)
def javascript_pagina_actual():
    return FileResponse(DIRECTORIO_PROYECTO / "script.js", media_type="text/javascript")


@app.exception_handler(RequestValidationError)
async def error_validacion(_request: Request, error: RequestValidationError):
    # No devolver inputs NaN/Infinity: no son serializables como JSON estándar.
    return JSONResponse(status_code=422, content={"detail": [
        {"type": fallo["type"], "loc": fallo["loc"], "msg": fallo["msg"]}
        for fallo in error.errors()
    ]})


def obtener_supabase() -> Client:
    global supabase
    if supabase is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise HTTPException(503, "La conexión con Supabase no está configurada.")
        try:
            supabase = create_client(url, key)
        except Exception:
            logger.error("Configuración de Supabase inválida")
            raise HTTPException(503, "La conexión con Supabase no está disponible.") from None
    return supabase


@app.get("/")
def read_root():
    return {"mensaje": "Finia API disponible"}


class SimulacionCreditoEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monto: float = Field(ge=0.01, le=1_000_000_000_000, allow_inf_nan=False)
    tasa_interes_anual: float = Field(
        ge=0, le=1000, allow_inf_nan=False,
        description="Tasa efectiva anual en porcentaje: 24 significa 24 % EA.",
    )
    plazo_meses: int = Field(
        gt=0, le=PLAZO_MAXIMO_MESES, strict=True,
        description="Plazo entero de 1 a 1200 meses.",
    )
    pago_extraordinario: float = Field(
        default=0, ge=0, le=1_000_000_000_000, allow_inf_nan=False,
        description="Abono adicional cada mes; mantiene la cuota y reduce el plazo.",
    )
    costo_seguro_por_millon: float = Field(
        default=1200, ge=0, le=1_000_000, allow_inf_nan=False,
        description="Supuesto de seguro mensual por millón de COP del monto inicial.",
    )


class CuotaAmortizacion(BaseModel):
    mes: int
    saldo_inicial: float
    pago_capital: float  # Incluye el pago extraordinario aplicado.
    pago_interes: float
    pago_extraordinario: float
    seguro_vida: float
    cuota: float  # Pago real del mes, con seguro y abono extraordinario.
    saldo_final: float


class SimulacionCreditoSalida(BaseModel):
    simulacion_id: UUID | None = None
    tipo_credito: str = Field(description="Clasificación orientativa por monto y plazo.")
    tasa_mensual: float = Field(description="Tasa mes vencido expresada en porcentaje.")
    cuota_financiera: float
    cuota_mensual: float = Field(description="Cuota base con seguro, sin abono extraordinario.")
    seguro_mensual: float
    total_intereses: float
    total_seguros: float
    costo_total_prestamo: float
    plazo_real_meses: int
    tabla_amortizacion: list[CuotaAmortizacion]


def dinero(valor: Decimal) -> Decimal:
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def tasa_ea_a_mensual(tasa_ea_porcentaje: float) -> Decimal:
    if tasa_ea_porcentaje == 0:
        return Decimal(0)
    with localcontext() as contexto:
        contexto.prec = 40
        return (Decimal(1) + Decimal(str(tasa_ea_porcentaje)) / 100) ** (
            Decimal(1) / 12
        ) - 1


def clasificar_credito(monto: Decimal, plazo_meses: int) -> str:
    """Heurística heredada del simulador; no determina elegibilidad crediticia."""
    if monto >= 80_000_000 and plazo_meses >= 120:
        return "Vivienda (Hipotecario)"
    if monto <= 5_000_000 and plazo_meses <= 36:
        return "Microcrédito o Tarjeta"
    if monto >= 20_000_000 and plazo_meses <= 72:
        return "Compra de Cartera / Vehículo"
    return "Libre Inversión"


def calcular_simulacion(datos: SimulacionCreditoEntrada) -> SimulacionCreditoSalida:
    """Motor único para JSON, Excel y persistencia; no accede a Supabase."""
    with localcontext() as contexto:
        contexto.prec = 40
        monto = dinero(Decimal(str(datos.monto)))
        tasa_mensual = tasa_ea_a_mensual(datos.tasa_interes_anual)
        plazo = datos.plazo_meses
        if tasa_mensual == 0:
            cuota_calculada = monto / plazo
        else:
            cuota_calculada = monto * tasa_mensual / (
                1 - (1 + tasa_mensual) ** (-plazo)
            )

        cuota_financiera = dinero(cuota_calculada)
        seguro = dinero(monto / 1_000_000 * Decimal(str(datos.costo_seguro_por_millon)))
        extra_solicitado = dinero(Decimal(str(datos.pago_extraordinario)))
        saldo = monto
        intereses = Decimal(0)
        seguros = Decimal(0)
        costo_total = Decimal(0)
        tabla = []

        for mes in range(1, plazo + 1):
            saldo_inicial = saldo
            interes = dinero(saldo * tasa_mensual)
            capital_ordinario = (
                saldo if mes == plazo else
                min(saldo, max(Decimal(0), cuota_financiera - interes))
            )
            extra = min(extra_solicitado, saldo - capital_ordinario)
            capital = capital_ordinario + extra
            saldo = saldo - capital
            cuota_real = capital + interes + seguro
            intereses += interes
            seguros += seguro
            costo_total += cuota_real
            tabla.append(CuotaAmortizacion(
                mes=mes, saldo_inicial=float(saldo_inicial),
                pago_capital=float(capital), pago_interes=float(interes),
                pago_extraordinario=float(extra), seguro_vida=float(seguro),
                cuota=float(cuota_real), saldo_final=float(saldo),
            ))
            if saldo == 0:
                break

        return SimulacionCreditoSalida(
            tipo_credito=clasificar_credito(monto, plazo),
            tasa_mensual=float((tasa_mensual * 100).quantize(PRECISION_TASA)),
            cuota_financiera=float(cuota_financiera),
            cuota_mensual=float(cuota_financiera + seguro),
            seguro_mensual=float(seguro), total_intereses=float(intereses),
            total_seguros=float(seguros), costo_total_prestamo=float(costo_total),
            plazo_real_meses=len(tabla), tabla_amortizacion=tabla,
        )


def obtener_usuario_id(
    authorization: str | None = Header(default=None),
    cliente: Client = Depends(obtener_supabase),
) -> UUID | None:
    """Permite simulaciones públicas; si hay token, verifica su identidad."""
    if authorization is None:
        return None
    esquema, _, token = authorization.partition(" ")
    if esquema.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Autorización inválida.", headers={"WWW-Authenticate": "Bearer"})
    try:
        respuesta = cliente.auth.get_user(token.strip())
        if respuesta.user is None:
            raise ValueError("Usuario ausente")
        return UUID(str(respuesta.user.id))
    except httpx.HTTPError:
        raise HTTPException(503, "No se pudo verificar la sesión.") from None
    except Exception:
        raise HTTPException(401, "Sesión inválida o expirada.", headers={"WWW-Authenticate": "Bearer"}) from None


def guardar_simulacion(
    datos: SimulacionCreditoEntrada,
    resultado: SimulacionCreditoSalida,
    cliente: Client,
    usuario_id: UUID | None,
) -> UUID:
    identificador = uuid4()
    tasa_mensual = tasa_ea_a_mensual(datos.tasa_interes_anual)
    registro = {
        "id": str(identificador),
        "usuario_id": str(usuario_id) if usuario_id else None,
        "monto": str(dinero(Decimal(str(datos.monto)))),
        "moneda": "COP", "plazo_meses": datos.plazo_meses,
        "sistema_amortizacion": "cuota_fija", "tipo_tasa": "fija",
        "tasa_nominal_anual": str((tasa_mensual * 12).quantize(PRECISION_TASA)),
        "capitalizaciones_por_anio": 12,
        "tasa_efectiva_anual": str((Decimal(str(datos.tasa_interes_anual)) / 100).quantize(PRECISION_TASA)),
        "tasa_periodica_mensual": str(tasa_mensual.quantize(PRECISION_TASA)),
        "cuota_inicial": str(resultado.cuota_mensual),
        "intereses_totales": str(resultado.total_intereses),
        "total_pagar": str(resultado.costo_total_prestamo),
        "version_calculo": VERSION_CALCULO,
        "supuestos": {
            "tasa_interes_anual_porcentaje": datos.tasa_interes_anual,
            "convencion_tasa": "efectiva_anual",
            "pago_extraordinario": datos.pago_extraordinario,
            "frecuencia_extra": "mensual_reduccion_plazo",
            "costo_seguro_por_millon": datos.costo_seguro_por_millon,
            "base_seguro": "monto_inicial",
            "seguro_mensual": resultado.seguro_mensual,
            "total_seguros": resultado.total_seguros,
            "cuota_financiera": resultado.cuota_financiera,
            "tipo_credito": resultado.tipo_credito,
            "plazo_real_meses": resultado.plazo_real_meses,
        },
        "amortizacion": [fila.model_dump() for fila in resultado.tabla_amortizacion],
    }
    try:
        # El UUID ya es conocido; no hace falta devolver toda la fila JSONB.
        cliente.table("simulaciones_credito").insert(registro, returning="minimal").execute()
    except Exception as error:
        logger.error("Guardado de simulación %s falló (%s)", identificador, type(error).__name__)
        raise HTTPException(503, "No se pudo confirmar el guardado de la simulación en Supabase.") from None
    return identificador


def generar_excel(resultado: SimulacionCreditoSalida, datos: SimulacionCreditoEntrada | None = None) -> bytes:
    from credito_excel import crear_libro_credito
    comparacion = None
    if datos is not None and datos.pago_extraordinario > 0:
        comparacion = calcular_simulacion(datos.model_copy(update={"pago_extraordinario": 0}))
    return crear_libro_credito(resultado, datos, comparacion)


class DiagnosticoFinancieroEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    empresa_id: UUID
    origen: Literal["web"] = "web"
    nombre_empresa: str | None = Field(default=None, min_length=1, max_length=200)
    sector_economico: str | None = Field(default=None, min_length=1, max_length=150)
    modelos_negocio: list[Annotated[str, Field(min_length=1, max_length=100)]] | None = Field(default=None, max_length=20)
    canales_venta: list[Annotated[str, Field(min_length=1, max_length=100)]] | None = Field(default=None, max_length=50)
    correo_contacto: str | None = Field(default=None, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    desafio_declarado: str = Field(min_length=1, max_length=4000)
    ingresos_mensuales: Decimal = Field(ge=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
    gastos_mensuales: Decimal = Field(ge=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
    servicio_deuda_mensual: Decimal = Field(ge=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
    flujo_caja_mensual: Decimal = Field(max_digits=18, decimal_places=2, allow_inf_nan=False)


class DiagnosticoFinancieroSalida(BaseModel):
    id: UUID
    empresa_id: UUID
    estado: Literal["preliminar"]


@app.post("/diagnosticos-financieros", status_code=201, response_model=DiagnosticoFinancieroSalida)
def crear_diagnostico_financiero(
    datos: DiagnosticoFinancieroEntrada,
    cliente: Client = Depends(obtener_supabase),
    usuario_id: UUID | None = Depends(obtener_usuario_id),
):
    if usuario_id is None:
        raise HTTPException(401, "Inicia sesión para guardar el formulario.", headers={"WWW-Authenticate": "Bearer"})

    try:
        empresa = cliente.table("empresas").select("id").eq(
            "id", str(datos.empresa_id)
        ).eq("propietario_usuario_id", str(usuario_id)).limit(1).execute()
    except Exception as error:
        logger.error("No se pudo verificar la empresa (%s)", type(error).__name__)
        raise HTTPException(503, "No se pudo verificar el acceso a la empresa.") from None

    if not empresa.data:
        raise HTTPException(403, "No tienes acceso a esta empresa.")

    identificador = uuid4()
    registro = datos.model_dump(mode="json", exclude_unset=True)
    registro["origen"] = datos.origen
    # Se captura el formulario; el motor de diagnóstico evaluará el puntaje después.
    registro.update(id=str(identificador), estado="preliminar", puntaje_salud=None)
    try:
        cliente.table("diagnosticos_financieros").insert(registro, returning="minimal").execute()
    except Exception as error:
        logger.error("Guardado de diagnóstico %s falló (%s)", identificador, type(error).__name__)
        raise HTTPException(503, "No se pudo confirmar el guardado del formulario.") from None

    return DiagnosticoFinancieroSalida(id=identificador, empresa_id=datos.empresa_id, estado="preliminar")


@app.post("/previsualizar-credito", response_model=SimulacionCreditoSalida)
def previsualizar_credito(datos: SimulacionCreditoEntrada):
    """Cálculo interactivo sin escritura ni dependencia de Supabase."""
    return calcular_simulacion(datos)


@app.post("/previsualizar-credito/excel")
def descargar_previsualizacion(datos: SimulacionCreditoEntrada):
    return Response(
        content=generar_excel(calcular_simulacion(datos), datos),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="Plan_Finia.xlsx"'},
    )


@app.post("/simular-credito", response_model=SimulacionCreditoSalida)
def simular_credito(
    datos: SimulacionCreditoEntrada,
    cliente: Client = Depends(obtener_supabase),
    usuario_id: UUID | None = Depends(obtener_usuario_id),
):
    resultado = calcular_simulacion(datos)
    resultado.simulacion_id = guardar_simulacion(datos, resultado, cliente, usuario_id)
    return resultado


@app.post("/simular-credito/excel")
def exportar_simulacion(
    datos: SimulacionCreditoEntrada,
    cliente: Client = Depends(obtener_supabase),
    usuario_id: UUID | None = Depends(obtener_usuario_id),
):
    resultado = calcular_simulacion(datos)
    contenido = generar_excel(resultado, datos)
    identificador = guardar_simulacion(datos, resultado, cliente, usuario_id)
    return Response(
        content=contenido,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="Simulacion_credito.xlsx"',
                 "X-Simulacion-Id": str(identificador)},
    )

