<?php

/*
 * Consulta el portal "Consulta y recuperacion de comprobantes" del SAT con la
 * e.firma de Monsort y devuelve el resultado como UN objeto JSON en stdout.
 *
 * Es el unico pedazo de PHP del proyecto. Existe porque la libreria madura
 * para hablar con el portal (phpcfdi/cfdi-sat-scraper) es de PHP y no hay
 * equivalente confiable en Python. Todo lo demas —base de datos, reglas,
 * scheduler, endpoints— vive en FastAPI: este script no toca la base.
 *
 * Lo invoca app/services/portal_sat_service.py. Uso manual (para probar):
 *
 *   SAT_CER_PATH=... SAT_KEY_PATH=... SAT_KEY_PASSWORD=... \
 *     php consultar_portal.php --tipo recibidos --desde 2026-09-01 --hasta 2026-09-01 --sin-xml
 *
 *   php consultar_portal.php --verificar-sesion        # solo prueba el login
 *
 * Opciones:
 *   --tipo recibidos|emitidos   (default recibidos)
 *   --desde AAAA-MM-DD          dia completo, hora de Mexico
 *   --hasta AAAA-MM-DD          dia completo, hora de Mexico (inclusivo)
 *   --dir RUTA                  carpeta donde guardar los XML (uuid.xml)
 *   --omitir ARCHIVO            un UUID por linea: NO descargar su XML (ya lo tenemos)
 *   --sin-xml                   solo metadata, sin descargar nada
 *   --verificar-sesion          solo iniciar sesion y salir
 *
 * Credenciales: SIEMPRE por variables de entorno, nunca por argumentos (los
 * argumentos se ven en `ps` para cualquier usuario del servidor).
 *   SAT_CER_PATH, SAT_KEY_PATH, SAT_KEY_PASSWORD
 *   SAT_PORTAL_SECLEVEL1=1      baja el nivel de cifrado SOLO para esta conexion
 *                               (el SAT usa llaves DH pequenas; ver README de la libreria)
 *
 * Codigos de salida: 0 ok, 2 configuracion/credencial, 3 login, 4 error del portal.
 * En todos los casos stdout trae JSON con "ok" y, si fallo, "tipo_error" y "error".
 */

declare(strict_types=1);

require __DIR__ . '/vendor/autoload.php';

use GuzzleHttp\Client;
use PhpCfdi\CfdiSatScraper\Exceptions\LoginException;
use PhpCfdi\CfdiSatScraper\Filters\DownloadType;
use PhpCfdi\CfdiSatScraper\NullMetadataMessageHandler;
use PhpCfdi\CfdiSatScraper\QueryByFilters;
use PhpCfdi\CfdiSatScraper\ResourceType;
use PhpCfdi\CfdiSatScraper\SatHttpGateway;
use PhpCfdi\CfdiSatScraper\SatScraper;
use PhpCfdi\CfdiSatScraper\Sessions\Fiel\FielSessionManager;
use PhpCfdi\Credentials\Credential;

const ZONA_MEXICO = 'America/Mexico_City';

function salir(array $resultado, int $codigo): never
{
    fwrite(STDOUT, json_encode(
        $resultado,
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE
    ));
    exit($codigo);
}

function fallar(string $tipo, string $mensaje, int $codigo, ?Throwable $e = null): never
{
    salir([
        'ok' => false,
        'tipo_error' => $tipo,
        'error' => $mensaje,
        'excepcion' => $e ? get_class($e) : null,
    ], $codigo);
}

/** Registra el caso extremo: 500+ CFDI en un mismo segundo (el portal no da mas). */
final class AvisosDeMetadata extends NullMetadataMessageHandler
{
    /** @var string[] */
    public array $avisos = [];

    public function maximum(DateTimeImmutable $moment): void
    {
        $this->avisos[] = 'Se alcanzo el tope de 500 registros en el segundo ' . $moment->format('c');
    }
}

$opciones = getopt('', ['tipo:', 'desde:', 'hasta:', 'dir:', 'omitir:', 'sin-xml', 'verificar-sesion']);

// --- credencial -----------------------------------------------------------
$rutaCer = getenv('SAT_CER_PATH') ?: '';
$rutaKey = getenv('SAT_KEY_PATH') ?: '';
$contrasena = getenv('SAT_KEY_PASSWORD');
if ('' === $rutaCer || '' === $rutaKey || false === $contrasena) {
    fallar('configuracion', 'Faltan SAT_CER_PATH, SAT_KEY_PATH o SAT_KEY_PASSWORD en el entorno', 2);
}

try {
    $credencial = Credential::openFiles($rutaCer, $rutaKey, $contrasena);
} catch (Throwable $e) {
    fallar('credencial', 'No se pudo abrir la e.firma (ruta o contrasena): ' . $e->getMessage(), 2, $e);
}
if (! $credencial->isFiel()) {
    fallar('credencial', 'El certificado no es e.firma (FIEL); parece un CSD', 2);
}
if (! $credencial->certificate()->validOn()) {
    fallar('credencial', 'La e.firma esta vencida o todavia no es valida', 2);
}

// --- cliente HTTP -----------------------------------------------------------
$opcionesCliente = ['connect_timeout' => 30, 'timeout' => 180];
if ('1' === getenv('SAT_PORTAL_SECLEVEL1')) {
    $opcionesCliente['curl'] = [CURLOPT_SSL_CIPHER_LIST => 'DEFAULT@SECLEVEL=1'];
}
$avisos = new AvisosDeMetadata();
$scraper = new SatScraper(
    FielSessionManager::create($credencial),
    new SatHttpGateway(new Client($opcionesCliente)),
    $avisos,
);

try {
    // Inicia sesion y confirma que la cookie sirve.
    $scraper->confirmSessionIsAlive();
} catch (LoginException $e) {
    fallar('login', 'El portal rechazo el inicio de sesion: ' . $e->getMessage(), 3, $e);
} catch (Throwable $e) {
    fallar('portal', 'No se pudo conectar al portal: ' . $e->getMessage(), 4, $e);
}

if (isset($opciones['verificar-sesion'])) {
    salir([
        'ok' => true,
        'rfc' => $credencial->rfc(),
        'mensaje' => 'Sesion iniciada correctamente',
        'certificado_vence' => $credencial->certificate()->validTo(),
    ], 0);
}

// --- consulta -----------------------------------------------------------
$tipo = $opciones['tipo'] ?? 'recibidos';
if (! in_array($tipo, ['recibidos', 'emitidos'], true)) {
    fallar('configuracion', "--tipo debe ser recibidos o emitidos, no '{$tipo}'", 2);
}
if (! isset($opciones['desde'], $opciones['hasta'])) {
    fallar('configuracion', 'Faltan --desde y --hasta (AAAA-MM-DD)', 2);
}

$zona = new DateTimeZone(ZONA_MEXICO);
try {
    $desde = new DateTimeImmutable($opciones['desde'] . ' 00:00:00', $zona);
    $hasta = new DateTimeImmutable($opciones['hasta'] . ' 23:59:59', $zona);
} catch (Throwable $e) {
    fallar('configuracion', 'Fecha invalida: ' . $e->getMessage(), 2, $e);
}

try {
    $consulta = new QueryByFilters(
        $desde,
        $hasta,
        'emitidos' === $tipo ? DownloadType::emitidos() : DownloadType::recibidos(),
    );
    // Estado: todos (vigentes y canceladas) es el default de la libreria.
    $lista = $scraper->listByPeriod($consulta);
} catch (Throwable $e) {
    fallar('portal', 'Fallo la consulta al portal: ' . $e->getMessage(), 4, $e);
}

$cfdis = [];
foreach ($lista as $metadata) {
    $fila = [];
    foreach ($metadata->getData() as $campo => $valor) {
        // Las ligas de descarga son de un solo uso y de esta sesion: no sirven fuera.
        if (str_starts_with($campo, 'url')) {
            continue;
        }
        $fila[$campo] = $valor;
    }
    $fila['tiene_xml'] = $metadata->hasResource(ResourceType::xml());
    $cfdis[] = $fila;
}

// --- descarga de XML -----------------------------------------------------------
$descargados = [];
$solicitados = 0;
$errorDescarga = null;

if (! isset($opciones['sin-xml'])) {
    $carpeta = $opciones['dir'] ?? '';
    if ('' === $carpeta) {
        fallar('configuracion', 'Falta --dir (o usa --sin-xml)', 2);
    }

    $omitir = [];
    if (isset($opciones['omitir']) && is_readable($opciones['omitir'])) {
        $omitir = array_values(array_filter(array_map(
            'trim',
            file($opciones['omitir'], FILE_IGNORE_NEW_LINES) ?: [],
        )));
    }

    $pendientes = $lista
        ->filterWithOutUuids($omitir)
        ->filterWithResourceLink(ResourceType::xml());
    $solicitados = count($pendientes);

    if ($solicitados > 0) {
        try {
            $descargados = $scraper
                ->resourceDownloader(ResourceType::xml(), $pendientes, 10)
                ->saveTo($carpeta, true, 0o700);
        } catch (Throwable $e) {
            // La metadata ya es util aunque falle la descarga: se reporta y se sigue.
            $errorDescarga = get_class($e) . ': ' . $e->getMessage();
        }
    }
}

try {
    $scraper->getSessionManager()->logout();
} catch (Throwable) {
    // cerrar sesion es cortesia; si falla no importa
}

salir([
    'ok' => true,
    'rfc' => $credencial->rfc(),
    'tipo' => $tipo,
    'desde' => $desde->format('Y-m-d'),
    'hasta' => $hasta->format('Y-m-d'),
    'total' => count($cfdis),
    'cfdis' => $cfdis,
    'xml_solicitados' => $solicitados,
    'xml_descargados' => array_values($descargados),
    'error_descarga' => $errorDescarga,
    'avisos' => $avisos->avisos,
], 0);
