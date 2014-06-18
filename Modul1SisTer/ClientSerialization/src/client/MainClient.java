package client;
import java.io.*;
import java.net.*;
import java.util.Scanner;

import messages.Message;

public class MainClient {
	public static void main (String[] Args) {
		Socket clientSocket;
		ObjectInputStream inpStream;
		ObjectOutputStream outStream;
		Scanner scan;
		String input;
		Message message;
		InetAddress ip;
		
		try {
			clientSocket = new Socket("192.168.1.8", 2222);
			outStream = new ObjectOutputStream(clientSocket.getOutputStream());
			ip = InetAddress.getLocalHost();
			System.out.println("Masukan nama hari atau ketik \"all\" untuk menampilkan semua :");
			scan = new Scanner(System.in);
			input = scan.nextLine();
			outStream.writeObject(new Message(ip.getHostAddress().toString(),input));
			outStream.flush();
			
			inpStream = new ObjectInputStream(clientSocket.getInputStream());
			try {
				message = (Message) inpStream.readObject();
				System.out.println("From "+message.getSender()+" : "+message.getMessage());
			} catch (ClassNotFoundException ex) {
				System.out.println("Error! : "+ex.getMessage());
			}
			
		} catch (IOException ex) {
			System.out.println("Error! : "+ex.getMessage());
		}
	}
}
