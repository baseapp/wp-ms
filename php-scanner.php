<?php
error_reporting(E_ERROR);

class CQtrPattern
{
	protected $_severity;
	protected $_pattern;
	protected $_details;

	public function __construct($severity,$pattern,$details)
	{
		$this->_severity = $severity;
		$this->_pattern  = $pattern;
		$this->_details  = $details;
	}

	public function severity(){
		return $this->_severity;
	}

	public function pattern(){
		return $this->_pattern;
	}

	public function details(){
		return $this->_details;
	}

	public function find_match($str)
	{
		$matches = array();
		try
		{
			$match = preg_match("/" . $this->_pattern . "/m", $str, $group);

			if ($match > 0)
			{
				array_push($matches, array($this,$group[0]));
			}
		}
		catch (Exception $e)
		{
			print "Error in" . $e->getMessage();
		}

		if( count($matches) == 0 )
		{
			return NULL;
		}

		return $matches;
	}
}


class FileScanner {
	protected $_database;

	public function __construct() {
		$this->_database = array();
	}

	public function load_db( $path ) {
		if ( ! is_file( $path ) ) {
			return false;
		}
		$file = fopen( $path, "r" );
		if ( ! $file ) {
			return false;
		}
		$body = fread( $file, filesize( $path ) );
		fclose( $file );
		$step1    = base64_decode( $body );
		$step2    = str_rot13( $step1 );
		$patterns = json_decode( $step2 );
		foreach ( $patterns as $entry ) {
			$pattern = new CQtrPattern(
				$entry[0], /* severity */
				$entry[1], /* pattern */
				$entry[2]  /* details */
			);

			array_push( $this->_database, $pattern );
		}
	}

	public function Scan($file_path)
	{
		$matches = array();

		if( !is_file($file_path)){
			return NULL;
		}

		$file = fopen($file_path,"r");
		if( !$file ){
			return NULL;
		}

		$file_size = filesize( $file_path );

		if(  $file_size <= 0 ){
			return NULL;
		}

		$body = fread($file,$file_size);
		fclose($file);
		foreach( $this->_database as $pattern ){
			$match = $pattern->find_match($body);
			if( $match != NULL ){
				$matches = array_merge($matches, $match);
			}
		}

		if( count($matches) == 0 ){
			return NULL;
		}

		return $matches;
	}
}

$options = getopt("f:i:");
if(array_key_exists('f', $options)){
    $db_file = $options['f'];
} else {
    fwrite(STDERR, "Database file not provided");
    exit(1);
}
if(array_key_exists('i', $options)){
	$input_file = $options['i'];
    if(! is_array($input_file)){
        $input_file = array($input_file);
    }
} else {
	fwrite(STDERR, "File not given for scan");
    exit(1);
}

$scanner = new FileScanner();
$scanner->load_db($db_file);
foreach ($input_file as $file){
	try{
		$matches = $scanner->Scan($file);
		if( $matches ) {
			foreach ( $matches as $match ) {
				$pattern = $match[0];
				echo $pattern->details();
			}
		}
	} catch (Exception $e){
		fwrite(STDERR, "Error in scan");
	}

}
