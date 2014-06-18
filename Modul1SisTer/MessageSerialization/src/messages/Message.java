package messages;
import java.io.Serializable;

public class Message implements Serializable {
	private String sender;
	private String message;
	
	public Message (String sender, String message) {
		this.sender = sender;
		this.message = message;
	}
	
	public String getMessage() {
		return message;
	}
	
	public String getSender() {
		return sender;
	}
}
