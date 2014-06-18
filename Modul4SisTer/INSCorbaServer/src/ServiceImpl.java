
public class ServiceImpl extends ServicePOA {
	public void ping( ) {
		System.out.println( "PingService.ping called..." );
	}

	public String hello(String mesg) {
		System.out.println(mesg);
		return mesg;
	}
}
